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

import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ocsf_mapper.apply import apply_stream_with_class
from ocsf_mapper.catalog import join_catalog
from ocsf_mapper.lint import lint_one
from ocsf_mapper.registry import list_mappings
from ocsf_mapper.schema import Schema
from ocsf_mapper.validate import validate


_HERE = Path(__file__).resolve().parent
_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def create_app(root: Optional[Path | str] = None) -> FastAPI:
    """Create the FastAPI app bound to ``root`` (defaults to cwd)."""
    root_path = Path(root) if root else Path.cwd()
    mappings_dir = root_path / "mappings"
    samples_dir = root_path / "samples"
    catalog_path = root_path / "catalog.json"

    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app = FastAPI(title="ocsf-mapper", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    # Cache schema across requests (it's read-only, slow to load fresh each time).
    schema = Schema()

    # ----- helpers --------------------------------------------------------

    def _row_for_card(entry: dict, registry_by_name: dict) -> dict:
        name = entry["source"]
        reg = registry_by_name.get(name) or {}
        sample_path = reg.get("sample")
        # Cheap lint: parse + validate the first 3 events of the pinned sample.
        # Full lint runs on the CLI; this is just for the card status pill.
        lint_status = "unknown"
        event_count = 0
        if sample_path:
            try:
                cfg = json.loads(Path(reg["path"]).read_text())
                lines = Path(sample_path).read_text().splitlines()[:50]
                events = list(apply_stream_with_class(cfg, lines))
                event_count = len(events)
                if events:
                    first_event, cls = events[0]
                    errs = validate(first_event, cls, schema=schema)
                    lint_status = "ok" if not errs else "fail"
            except Exception:
                lint_status = "fail"
        return {
            **entry,
            "lint_status": lint_status,
            "event_count": event_count,
            "has_mapping": entry["status"] == "mapped",
            "sample_path": sample_path,
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
            {"rows": rows, "totals": _summarize(rows)},
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
                from ocsf_mapper.apply import apply_with_class
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
    return {"total": total, "ok": ok, "fail": fail, "by_priority": by_pri, "by_category": by_cat}
