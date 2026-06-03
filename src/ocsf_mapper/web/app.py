"""FastAPI app factory for the local web UI.

Routes (session 1 scope):

    GET  /                       homepage card grid
    GET  /sources/{name}         per-source detail page (tabs: sample, output)
    GET  /sources/{name}/sample  HTMX fragment: pinned sample preview
    POST /sources/{name}/apply   HTMX fragment: side-by-side raw / OCSF for uploaded log
    GET  /healthz                liveness probe

The app reads from a configurable ``root`` directory containing ``mappings/``,
``samples/``, and ``catalog.json``. Defaults to ``cwd``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ocsf_mapper.apply import apply_stream_with_class, apply_with_class
from ocsf_mapper.catalog import join_catalog
from ocsf_mapper.coverage import coverage, summary as coverage_summary
from ocsf_mapper.lint import lint_one
from ocsf_mapper.registry import list_mappings
from ocsf_mapper.schema import Schema, list_available_versions
from ocsf_mapper.stream import tail_file
from ocsf_mapper.validate import validate


_HERE = Path(__file__).resolve().parent
_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _slug(s: str) -> str:
    """URL-safe lowercase token derived from a human label.

    Used for tree-node hashes and card filter attributes — needs to be
    stable across page renders, not pretty.
    """
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _build_snippets(name: str, cfg: dict, sample_filename: Optional[str]) -> list[dict]:
    """Per-mapping copy-paste code blocks for the source page's Snippets tab.

    Each block is templated with the actual mapping path + pinned sample so
    a user can paste straight into their shell / notebook / cluster.
    """
    import textwrap
    mapping_path = f"mappings/{name}.json"
    sample_path  = f"samples/{sample_filename}" if sample_filename else f"samples/{name}.log"

    cli = (
        f"ocsf-mapper apply {mapping_path} \\\n"
        f"    {sample_path} out.jsonl"
    )

    python_sdk = textwrap.dedent(f"""\
        import json
        from ocsf_mapper.apply import apply_stream_with_class

        config = json.loads(open("{mapping_path}").read())
        with open("{sample_path}") as f:
            for event, cls in apply_stream_with_class(config, f):
                print(cls, event)""")

    pyspark = textwrap.dedent(f"""\
        # See examples/spark/cloudtrail_udf.py for a full runnable version.
        from pyspark.sql import SparkSession, functions as F
        from pyspark.sql.types import StringType
        import json

        spark = SparkSession.builder.appName("ocsf-{name}").getOrCreate()
        config = json.loads(open("{mapping_path}").read())
        config_bc = spark.sparkContext.broadcast(config)

        def to_ocsf(raw):
            from ocsf_mapper.apply import apply
            e = apply(config_bc.value, raw)
            return json.dumps(e) if e else None

        to_ocsf_udf = F.udf(to_ocsf, StringType())
        (spark.read.text("s3://raw-logs/{name}/")
             .withColumn("ocsf", to_ocsf_udf(F.col("value")))
             .write.partitionBy("class_uid")
             .parquet("s3://ocsf-lake/{name}/"))""")

    pandas = textwrap.dedent(f"""\
        # Row-wise apply is fine up to ~100K rows; use PySpark above for bigger.
        import json, pandas as pd
        from ocsf_mapper.apply import apply

        config = json.loads(open("{mapping_path}").read())
        df = pd.read_json("{sample_path}", lines=True)
        df["ocsf"] = df.apply(lambda r: apply(config, r.to_json()), axis=1)""")

    return [
        {"title": "CLI",                          "lang": "bash",   "code": cli},
        {"title": "Python (SDK)",                 "lang": "python", "code": python_sdk},
        {"title": "PySpark (UDF)",                "lang": "python", "code": pyspark},
        {"title": "Pandas (batch, ≤100K rows)",  "lang": "python", "code": pandas},
    ]


def create_app(root: Optional[Path | str] = None) -> FastAPI:
    """Create the FastAPI app bound to ``root`` (defaults to cwd)."""
    root_path = Path(root) if root else Path.cwd()
    mappings_dir = root_path / "mappings"
    samples_dir = root_path / "samples"
    catalog_path = root_path / "catalog.json"

    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    # Cache-bust /static/main.css with its mtime so a stale browser cache
    # doesn't mask a CSS edit. Computed once at startup; rebooting the
    # server picks up new edits.
    _css = _HERE / "static" / "main.css"
    templates.env.globals["asset_v"] = (
        str(int(_css.stat().st_mtime)) if _css.exists() else "1"
    )
    app = FastAPI(title="ocsf-mapper", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    # Cache schemas across requests (read-only, file-bound). The default
    # schema (current submodule) is always loaded. Pinned alternates from
    # list_available_versions() load lazily on first per-version request.
    schema = Schema()
    _schema_cache: dict[str, Schema] = {}

    def _get_schema(version: Optional[str] = None) -> Schema:
        if not version:
            return schema
        cached = _schema_cache.get(version)
        if cached is not None:
            return cached
        s = Schema(version=version)
        _schema_cache[version] = s
        return s

    # ----- helpers --------------------------------------------------------

    def _row_for_card(entry: dict, registry_by_name: dict) -> dict:
        name = entry["source"]
        reg = registry_by_name.get(name) or {}
        sample_path = reg.get("sample")
        # Cheap lint: parse + validate the first 3 events of the pinned sample.
        # Full lint runs on the CLI; this is just for the card status pill.
        lint_status = "unknown"
        event_count = 0
        cov_summary = None
        if reg.get("path"):
            try:
                cfg = json.loads(Path(reg["path"]).read_text())
                cov_summary = coverage_summary(coverage(cfg, schema))
                if sample_path:
                    lines = Path(sample_path).read_text().splitlines()[:50]
                    events = list(apply_stream_with_class(cfg, lines))
                    event_count = len(events)
                    if events:
                        first_event, cls = events[0]
                        errs = validate(first_event, cls, schema=schema)
                        lint_status = "ok" if not errs else "fail"
            except Exception:
                lint_status = "fail"
        ocsf = entry.get("ocsf", {})
        return {
            **entry,
            "lint_status": lint_status,
            "event_count": event_count,
            "has_mapping": entry["status"] == "mapped",
            "sample_path": sample_path,
            "coverage": cov_summary,
            "cat_slug": _slug(ocsf.get("category_name", "")),
            "cls_slug": _slug(ocsf.get("class_name", "")),
        }

    def _sorted_catalog_rows() -> list[dict]:
        rows = join_catalog(catalog_path, mappings_dir)
        registry_by_name = {m["name"]: m for m in list_mappings(mappings_dir)}
        enriched = [_row_for_card(e, registry_by_name) for e in rows]
        enriched.sort(key=lambda r: (_PRIORITY_RANK.get(r["priority"], 99), r["display_name"]))
        return enriched

    def _mapping_or_404(name: str) -> dict:
        p = mappings_dir / f"{name}.json"
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"unknown source: {name}")
        return json.loads(p.read_text())

    # ----- routes ---------------------------------------------------------

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "schema_version": schema.version()}

    @app.get("/", response_class=HTMLResponse)
    def homepage(request: Request) -> HTMLResponse:
        rows = _sorted_catalog_rows()
        return templates.TemplateResponse(
            request,
            "home.html",
            {"rows": rows, "totals": _summarize(rows), "tree": _build_tree(rows)},
        )

    @app.get("/new", response_class=HTMLResponse)
    def wizard_landing(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "wizard.html", {"step": 1, "categories": _PRIORITY_RANK.keys()},
        )

    @app.post("/new/draft", response_class=HTMLResponse)
    async def wizard_draft(
        request: Request,
        source_name: str = Form(...),
        vendor: str = Form(...),
        priority: str = Form("medium"),
        description: str = Form(""),
        display_name: str = Form(""),
        sample: UploadFile = File(...),
    ) -> HTMLResponse:
        import re
        if not re.fullmatch(r"[a-z][a-z0-9_]*", source_name):
            return templates.TemplateResponse(
                request,
                "partials/wizard_error.html",
                {"message": (
                    f"Invalid source_name: {source_name!r}. "
                    "Use lowercase letters, digits, and underscores, "
                    "starting with a letter."
                )},
                status_code=400,
            )
        # Decide sample filename by extension.
        suffix = Path(sample.filename or "sample.log").suffix or ".log"
        sample_target = samples_dir / f"{source_name}{suffix}"
        samples_dir.mkdir(parents=True, exist_ok=True)
        sample_target.write_bytes(await sample.read())

        # Call the generator.
        try:
            from ocsf_mapper.generate import generate
            from ocsf_mapper.providers import get_provider
            provider = get_provider()
            draft = generate(sample_target, source_name,
                             provider=provider, schema=schema)
        except Exception as e:
            return templates.TemplateResponse(
                request,
                "partials/wizard_error.html",
                {"message": (
                    f"LLM generator failed: {e!r}. "
                    "Set ANTHROPIC_API_KEY or OPENAI_API_KEY, "
                    "or OCSF_LLM_PROVIDER=fixture for offline use."
                )},
                status_code=500,
            )

        # Inject the user-provided catalog metadata so the draft is self-describing.
        draft = {
            "source_name": source_name,
            "display_name": display_name or source_name.replace("_", " ").title(),
            "vendor": vendor,
            "priority": priority,
            "description": description or f"Mapping for {source_name}.",
            **{k: v for k, v in draft.items() if k not in ("source_name",)},
        }

        return templates.TemplateResponse(
            request,
            "partials/wizard_draft.html",
            {
                "source_name": source_name,
                "sample_filename": sample_target.name,
                "draft_json": json.dumps(draft, indent=2),
            },
        )

    @app.post("/new/save", response_class=HTMLResponse)
    def wizard_save(
        request: Request,
        source_name: str = Form(...),
        content: str = Form(...),
    ) -> HTMLResponse:
        import re
        if not re.fullmatch(r"[a-z][a-z0-9_]*", source_name):
            raise HTTPException(status_code=400, detail="invalid source_name")
        target = mappings_dir / f"{source_name}.json"
        if target.exists():
            return templates.TemplateResponse(
                request,
                "partials/wizard_error.html",
                {"message": (
                    f"mappings/{source_name}.json already exists — refusing "
                    "to overwrite. Edit the existing source page instead."
                )},
                status_code=409,
            )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            return templates.TemplateResponse(
                request,
                "partials/wizard_error.html",
                {"message": f"invalid JSON: {e}"},
                status_code=400,
            )
        # Find the sample we just saved.
        sample_path = _find_sample_path(source_name)
        if sample_path is None:
            # Try a glob — the wizard may have saved e.g. .log when none existed.
            candidates = list(samples_dir.glob(f"{source_name}.*"))
            sample_path = candidates[0] if candidates else None

        # Lint before writing.
        from ocsf_mapper.audit import log_edit
        bytes_after = len(content.encode("utf-8"))
        tmp_path = target.with_suffix(".json.tmp")
        mappings_dir.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(parsed, indent=2) + "\n")
        try:
            result = lint_one(tmp_path, sample_path, schema)
            if result["status"] != "OK":
                log_edit(root_path, mapping=source_name, action="create",
                         lint_status=result["status"], errors=result["errors"],
                         bytes_before=0, bytes_after=bytes_after)
                return templates.TemplateResponse(
                    request,
                    "partials/wizard_error.html",
                    {"message": "Draft did not lint clean:\n" + "\n".join(result["errors"])},
                    status_code=400,
                )
            tmp_path.replace(target)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

        log_edit(root_path, mapping=source_name, action="create",
                 lint_status="OK", bytes_before=0, bytes_after=bytes_after)
        return templates.TemplateResponse(
            request,
            "partials/wizard_done.html",
            {
                "source_name": source_name,
                "events": result.get("events", 0),
                "classes": result.get("classes", []),
            },
        )

    @app.get("/sources/{name}", response_class=HTMLResponse)
    def source_page(name: str, request: Request) -> HTMLResponse:
        cfg = _mapping_or_404(name)
        entry = next((e for e in _sorted_catalog_rows() if e["source"] == name), None)
        sample_path = entry["sample_path"] if entry else None
        sample_text = Path(sample_path).read_text() if sample_path else ""
        return templates.TemplateResponse(
            request,
            "source.html",
            {
                "name": name,
                "entry": entry,
                "cfg": cfg,
                "sample_text": sample_text,
                "sample_filename": Path(sample_path).name if sample_path else None,
            },
        )

    @app.get("/sources/{name}/sample", response_class=HTMLResponse)
    def source_sample(name: str, request: Request) -> HTMLResponse:
        entry = next((e for e in _sorted_catalog_rows() if e["source"] == name), None)
        if not entry or not entry["sample_path"]:
            return HTMLResponse('<div class="empty">No pinned sample.</div>')
        text = Path(entry["sample_path"]).read_text()
        return templates.TemplateResponse(
            request,
            "partials/sample.html",
            {
                "sample_text": text,
                "filename": Path(entry["sample_path"]).name,
                "line_count": text.count("\n"),
            },
        )

    @app.get("/sources/{name}/tail")
    async def source_tail(
        name: str,
        request: Request,
        file: str = Query(..., description="Absolute path to log file on this machine"),
        from_start: bool = Query(False),
        max_events: int = Query(0, ge=0, description="Stop after N events (0 = stream indefinitely)"),
    ) -> StreamingResponse:
        """SSE stream: tail a local log file through the named mapping in real time."""
        cfg = _mapping_or_404(name)
        file_path = Path(file).resolve()
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {file}")

        stop = threading.Event()
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _tail_worker() -> None:
            try:
                for raw in tail_file(file_path, poll_interval=0.3,
                                     from_start=from_start, stop=stop):
                    if stop.is_set():
                        break
                    line = raw.rstrip("\n")
                    if not line.strip():
                        continue
                    try:
                        pair = apply_with_class(cfg, line)
                    except Exception as exc:
                        item: dict = {"raw": line, "error": f"parse error: {exc!r}"}
                    else:
                        if pair is None:
                            item = {"raw": line, "error": "no match for parser/routing"}
                        else:
                            event, cls = pair
                            errs = validate(event, cls, schema=schema)
                            item = {
                                "raw": line, "event": event,
                                "class_name": cls, "validation": errs,
                            }
                    if not stop.is_set():
                        asyncio.run_coroutine_threadsafe(
                            queue.put(item), loop
                        ).result(timeout=5)
            except Exception:
                pass
            finally:
                try:
                    asyncio.run_coroutine_threadsafe(
                        queue.put(None), loop
                    ).result(timeout=2)
                except Exception:
                    pass

        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="ocsf-tail"
        )
        executor.submit(_tail_worker)

        async def event_stream():
            emitted = 0
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    if item is None:
                        break
                    yield f"data: {json.dumps(item)}\n\n"
                    emitted += 1
                    if max_events > 0 and emitted >= max_events:
                        break
            finally:
                stop.set()
                executor.shutdown(wait=False)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.post("/sources/{name}/apply", response_class=HTMLResponse)
    async def source_apply(
        name: str,
        request: Request,
        file: UploadFile = File(...),
    ) -> HTMLResponse:
        cfg = _mapping_or_404(name)
        raw = (await file.read()).decode("utf-8", errors="replace")
        lines = raw.splitlines()
        results: list[dict] = []
        for line in lines[:200]:  # cap UI rendering at 200 events for now
            if not line.strip():
                continue
            try:
                pair = apply_with_class(cfg, line)
            except Exception as e:
                results.append({"raw": line, "error": f"parse error: {e!r}"})
                continue
            if pair is None:
                results.append({"raw": line, "error": "no match for parser/routing"})
                continue
            event, cls = pair
            errs = validate(event, cls, schema=schema)
            results.append({
                "raw": line,
                "event": event,
                "class_name": cls,
                "validation": errs,
            })
        return templates.TemplateResponse(
            request,
            "partials/output.html",
            {
                "results": results,
                "total": len(results),
                "ok": sum(1 for r in results if not r.get("error") and not r.get("validation")),
                "warn": sum(1 for r in results if r.get("validation")),
                "fail": sum(1 for r in results if r.get("error")),
            },
        )

    @app.get("/sources/{name}/coverage", response_class=HTMLResponse)
    def source_coverage(name: str, request: Request) -> HTMLResponse:
        cfg = _mapping_or_404(name)
        cov = coverage(cfg, schema)
        return templates.TemplateResponse(
            request,
            "partials/coverage.html",
            {
                "name": name,
                "coverage": cov,
                "summary": coverage_summary(cov),
            },
        )

    @app.post("/sources/{name}/fix-with-ai")
    def source_fix_with_ai(
        name: str,
        content: str = Form(...),
        schema_version: str = Form(""),
    ) -> JSONResponse:
        """Ask the configured LLM provider to repair a broken mapping.

        Re-runs the linter against the in-progress Monaco content,
        captures the errors, and asks the LLM to produce a fixed
        mapping. Returns the fix as a JSON string the frontend stuffs
        into the editor buffer; the user still has to click Save,
        which re-runs the gate.
        """
        from ocsf_mapper.generate import fix_mapping
        from ocsf_mapper.providers import get_provider

        path = mappings_dir / f"{name}.json"
        if not path.exists():
            return JSONResponse(
                {"ok": False, "error": f"unknown source: {name}"},
                status_code=404,
            )

        # 1. Parse — fix flow requires a valid-JSON starting point.
        try:
            current = json.loads(content)
        except json.JSONDecodeError as e:
            return JSONResponse(
                {"ok": False, "error": f"invalid JSON: {e}. Fix the syntax first."},
                status_code=400,
            )

        # 2. Resolve target schema (default or pinned alternate).
        try:
            target_schema = _get_schema(schema_version or None)
        except FileNotFoundError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

        # 3. Re-run lint to capture current errors. We write to a sibling
        #    tmp file mirroring the save flow — keeps lint_one's I/O shape.
        sample_path = _find_sample_path(name)
        tmp_path = path.with_suffix(".json.fixtmp")
        tmp_path.write_text(json.dumps(current, indent=2) + "\n")
        try:
            result = lint_one(tmp_path, sample_path, target_schema)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
        if result["status"] == "OK":
            return JSONResponse(
                {"ok": False, "error": "Nothing to fix — mapping lints clean."},
                status_code=400,
            )

        sample_lines: list[str] = []
        if sample_path and sample_path.exists():
            sample_lines = [
                ln for ln in sample_path.read_text().splitlines() if ln.strip()
            ][:5]

        # 4. Call the LLM. Surface every failure mode as a clear string.
        try:
            provider = get_provider()
            fixed = fix_mapping(
                current,
                result["errors"],
                sample_lines,
                provider=provider,
                schema=target_schema,
            )
        except RuntimeError as e:
            # get_provider() raises RuntimeError when no key is configured.
            return JSONResponse(
                {
                    "ok": False,
                    "error": str(e),
                    "code": "no_provider",
                },
                status_code=503,
            )
        except (json.JSONDecodeError, ValueError) as e:
            return JSONResponse(
                {"ok": False, "error": f"LLM response was not valid JSON: {e}"},
                status_code=502,
            )
        except Exception as e:  # network, rate-limit, etc.
            return JSONResponse(
                {"ok": False, "error": f"LLM call failed: {e!r}"},
                status_code=502,
            )

        return JSONResponse({
            "ok": True,
            "mapping": json.dumps(fixed, indent=2),
            "n_errors_fixed": len(result["errors"]),
            "schema_version": target_schema.version(),
            "provider": getattr(provider, "name", "unknown"),
        })

    @app.get("/sources/{name}/snippets", response_class=HTMLResponse)
    def source_snippets(name: str, request: Request) -> HTMLResponse:
        cfg = _mapping_or_404(name)
        entry = next((e for e in _sorted_catalog_rows() if e["source"] == name), None)
        sample_filename = (
            Path(entry["sample_path"]).name if entry and entry.get("sample_path") else None
        )
        return templates.TemplateResponse(
            request,
            "partials/snippets.html",
            {"name": name, "snippets": _build_snippets(name, cfg, sample_filename)},
        )

    @app.get("/sources/{name}/validation", response_class=HTMLResponse)
    def source_validation(name: str, request: Request) -> HTMLResponse:
        cfg = _mapping_or_404(name)
        sample_path = _find_sample_path(name)
        if sample_path is None:
            return HTMLResponse('<div class="empty">No pinned sample to validate against.</div>')

        # Validate every event of the pinned sample. Capture per-event status +
        # aggregate counts of unique issues per class for the summary.
        from collections import Counter
        lines = sample_path.read_text().splitlines()
        events: list[dict] = []
        per_class_required: dict = {}  # class_name -> Counter of required-attr names always missing
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                pair = apply_with_class(cfg, line)
            except Exception as e:
                events.append({"index": i, "status": "fail",
                               "errors": [f"apply crashed: {e!r}"]})
                continue
            if pair is None:
                events.append({"index": i, "status": "skip",
                               "errors": ["line did not match parser / routing"]})
                continue
            ev, cls = pair
            errs = validate(ev, cls, schema=schema)
            events.append({
                "index": i,
                "status": "ok" if not errs else "fail",
                "class_name": cls,
                "errors": errs,
            })
            if errs:
                ct = per_class_required.setdefault(cls, Counter())
                for e in errs:
                    ct[e] += 1
        ok    = sum(1 for e in events if e["status"] == "ok")
        fail  = sum(1 for e in events if e["status"] == "fail")
        skip  = sum(1 for e in events if e["status"] == "skip")
        # Top 5 recurring failures across the sample, with class context.
        top_issues = []
        for cls, ct in per_class_required.items():
            for issue, n in ct.most_common(5):
                top_issues.append({"class_name": cls, "issue": issue, "count": n})
        top_issues.sort(key=lambda r: r["count"], reverse=True)

        return templates.TemplateResponse(
            request,
            "partials/validation.html",
            {
                "name": name,
                "events": events[:200],         # cap UI to first 200
                "total": len(events),
                "ok": ok,
                "fail": fail,
                "skip": skip,
                "top_issues": top_issues[:10],
                "sample_filename": sample_path.name,
            },
        )

    @app.get("/sources/{name}/mapping", response_class=HTMLResponse)
    def source_mapping(name: str, request: Request) -> HTMLResponse:
        cfg = _mapping_or_404(name)
        return templates.TemplateResponse(
            request,
            "partials/mapping_editor.html",
            {
                "name": name,
                "mapping_json": json.dumps(cfg, indent=2),
                "schema_versions": list_available_versions(),
            },
        )

    @app.post("/sources/{name}/save", response_class=HTMLResponse)
    def source_save(
        name: str,
        request: Request,
        content: str = Form(...),
        schema_version: str = Form(""),
    ) -> HTMLResponse:
        from ocsf_mapper.audit import log_edit
        path = mappings_dir / f"{name}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"unknown source: {name}")

        bytes_before = path.stat().st_size
        bytes_after = len(content.encode("utf-8"))

        try:
            target_schema = _get_schema(schema_version or None)
        except FileNotFoundError as e:
            errs = [str(e)]
            return templates.TemplateResponse(
                request,
                "partials/save_result.html",
                {"ok": False, "errors": errs, "schema_version": schema_version},
                status_code=400,
            )

        # 1. JSON syntax check
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            errs = [f"invalid JSON: {e}"]
            log_edit(root_path, mapping=name, action="update",
                     lint_status="REJECTED", errors=errs,
                     bytes_before=bytes_before, bytes_after=bytes_after)
            return templates.TemplateResponse(
                request,
                "partials/save_result.html",
                {"ok": False, "errors": errs, "schema_version": target_schema.version()},
                status_code=400,
            )

        # 2. Server-side lint: write to a sibling tmp file, run lint_one against
        #    the pinned sample, only promote to the real file if the result is OK.
        sample_path = _find_sample_path(name)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(parsed, indent=2) + "\n")
        try:
            result = lint_one(tmp_path, sample_path, target_schema)
            if result["status"] != "OK":
                errs = result["errors"] or [result["status"]]
                log_edit(root_path, mapping=name, action="update",
                         lint_status=result["status"], errors=errs,
                         bytes_before=bytes_before, bytes_after=bytes_after)
                return templates.TemplateResponse(
                    request,
                    "partials/save_result.html",
                    {"ok": False, "errors": errs, "schema_version": target_schema.version()},
                    status_code=400,
                )
            tmp_path.replace(path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

        log_edit(root_path, mapping=name, action="update",
                 lint_status="OK", bytes_before=bytes_before, bytes_after=bytes_after)
        return templates.TemplateResponse(
            request,
            "partials/save_result.html",
            {"ok": True, "errors": [],
             "events": result.get("events", 0),
             "classes": result.get("classes", []),
             "schema_version": target_schema.version()},
        )

    def _find_sample_path(name: str) -> Optional[Path]:
        for entry in list_mappings(mappings_dir):
            if entry["name"] == name and entry["sample"]:
                return Path(entry["sample"])
        return None

    # -- audit log view ----------------------------------------------------

    @app.get("/audit", response_class=HTMLResponse)
    def audit_view(request: Request) -> HTMLResponse:
        from ocsf_mapper.audit import read_audit
        cap = 500
        events = read_audit(root_path, limit=cap)
        return templates.TemplateResponse(
            request, "audit.html",
            {"events": events, "n": len(events), "cap": cap},
        )

    # -- Prometheus /metrics ----------------------------------------------

    @app.get("/metrics", response_class=HTMLResponse)
    def metrics() -> HTMLResponse:
        """Prometheus exposition format. Stdlib output — no client dep."""
        from ocsf_mapper.audit import read_audit
        rows = _sorted_catalog_rows()
        ok    = sum(1 for r in rows if r["lint_status"] == "ok")
        fail  = sum(1 for r in rows if r["lint_status"] == "fail")
        cov_scores = [r["coverage"]["score"] for r in rows if r.get("coverage")]
        avg_score = sum(cov_scores) / len(cov_scores) if cov_scores else 0
        audit_events = read_audit(root_path)
        save_ok   = sum(1 for e in audit_events if e["lint_status"] == "OK")
        save_fail = sum(1 for e in audit_events if e["lint_status"] in ("FAIL", "REJECTED"))

        lines = [
            "# HELP ocsf_mappings_total Number of mappings in the catalog.",
            "# TYPE ocsf_mappings_total gauge",
            f"ocsf_mappings_total {len(rows)}",
            "# HELP ocsf_mappings_lint_ok Number of mappings whose pinned sample lints clean.",
            "# TYPE ocsf_mappings_lint_ok gauge",
            f"ocsf_mappings_lint_ok {ok}",
            "# HELP ocsf_mappings_lint_fail Number of mappings whose pinned sample lints with errors.",
            "# TYPE ocsf_mappings_lint_fail gauge",
            f"ocsf_mappings_lint_fail {fail}",
            "# HELP ocsf_mappings_coverage_avg Average weighted coverage score (required+recommended).",
            "# TYPE ocsf_mappings_coverage_avg gauge",
            f"ocsf_mappings_coverage_avg {avg_score:.4f}",
            "# HELP ocsf_mapping_edits_total Number of audited mapping edit events.",
            "# TYPE ocsf_mapping_edits_total counter",
            f"ocsf_mapping_edits_total {len(audit_events)}",
            "# HELP ocsf_mapping_edits_saved_total Audited edits that committed to disk.",
            "# TYPE ocsf_mapping_edits_saved_total counter",
            f"ocsf_mapping_edits_saved_total {save_ok}",
            "# HELP ocsf_mapping_edits_rejected_total Audited edits the lint gate blocked.",
            "# TYPE ocsf_mapping_edits_rejected_total counter",
            f"ocsf_mapping_edits_rejected_total {save_fail}",
            "# HELP ocsf_schema_version_info Current OCSF schema version (label-only metric).",
            "# TYPE ocsf_schema_version_info gauge",
            f'ocsf_schema_version_info{{version="{schema.version()}"}} 1',
        ]
        return HTMLResponse(
            content="\n".join(lines) + "\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app


def _summarize(rows: list[dict]) -> dict:
    """Tally counts for the homepage status strip."""
    total = len(rows)
    by_pri: dict = {}
    by_cat: dict = {}
    ok = sum(1 for r in rows if r["lint_status"] == "ok")
    fail = sum(1 for r in rows if r["lint_status"] == "fail")
    for r in rows:
        by_pri[r["priority"]] = by_pri.get(r["priority"], 0) + 1
        cn = r["ocsf"]["category_name"]
        by_cat[cn] = by_cat.get(cn, 0) + 1
    return {
        "total": total, "ok": ok, "fail": fail,
        "by_priority": by_pri, "by_category": by_cat,
        "class_count": len({r["ocsf"]["class_name"] for r in rows}),
        "category_count": len(by_cat),
    }


def _build_tree(rows: list[dict]) -> list[dict]:
    """Group rows into OCSF category → class nodes for the homepage rail.

    Ordered by OCSF category_uid (derived from class_uid // 1000) so the
    tree renders in canonical schema order: System Activity (1), Findings (2),
    IAM (3), Network (4), Discovery (5), Application Activity (6),
    Remediation (7), Unmanned Systems (8).
    """
    # Key by category_name so OCSF extension classes (e.g. windows_registry
    # at class_uid 201xxx) fold into their parent category rather than
    # spawning a separate node.
    by_cat: dict[str, dict] = {}
    for r in rows:
        ocsf = r["ocsf"]
        try:
            cls_uid = int(ocsf["class_uid"])
        except (KeyError, ValueError, TypeError):
            continue
        cat_uid = cls_uid // 1000
        cat_name = ocsf.get("category_name", "Uncategorised")
        cls_name = ocsf.get("class_name", "Unknown")
        node = by_cat.setdefault(cat_name, {
            "category": cat_name,
            "category_uid": cat_uid,
            "slug": _slug(cat_name),
            "count": 0,
            "classes": {},
        })
        node["category_uid"] = min(node["category_uid"], cat_uid)
        node["count"] += 1
        cls = node["classes"].setdefault(cls_name, {
            "class_name": cls_name,
            "class_uid": cls_uid,
            "slug": _slug(cls_name),
            "count": 0,
        })
        cls["count"] += 1

    out = []
    for node in sorted(by_cat.values(), key=lambda n: n["category_uid"]):
        node["classes"] = sorted(node["classes"].values(), key=lambda c: c["class_uid"])
        out.append(node)
    return out
