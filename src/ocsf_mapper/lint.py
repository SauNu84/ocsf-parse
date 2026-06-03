"""CI gate: run every mapping against its pinned sample, validate each event.

Exit code is 0 iff every mapping in ``mappings/`` parses, maps, and validates
without errors. Run via:

    python -m ocsf_mapper.lint [mappings_folder]
"""

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from ocsf_mapper.apply import apply_stream_with_class
from ocsf_mapper.registry import list_mappings
from ocsf_mapper.schema import Schema
from ocsf_mapper.validate import validate


_COALESCE_THRESHOLD = 3


def _coalesce_event_errors(items: list[tuple[int, str, str]]) -> list[str]:
    """Collapse runs of identical (class, error_text) into one summary line.

    ``items`` is ``(event_index, class, error_text)`` in 1-based event
    order. When ≥``_COALESCE_THRESHOLD`` events share the same (class,
    error_text), they collapse to one entry — otherwise each event is
    listed individually. Keeps the output readable when a 100-event
    sample produces 100 copies of the same missing-attribute failure.
    """
    groups: "OrderedDict[tuple[str, str], list[int]]" = OrderedDict()
    for idx, cls, err in items:
        groups.setdefault((cls, err), []).append(idx)

    out: list[str] = []
    for (cls, err), idxs in groups.items():
        if len(idxs) >= _COALESCE_THRESHOLD:
            idxs_sorted = sorted(idxs)
            lo, hi = idxs_sorted[0], idxs_sorted[-1]
            if idxs_sorted == list(range(lo, hi + 1)):
                tag = f"events #{lo}-#{hi}"
            else:
                tag = f"events #{lo},…,#{hi} (×{len(idxs_sorted)})"
            out.append(f"{tag} ({cls}): {err}")
        else:
            for i in idxs:
                out.append(f"event #{i} ({cls}): {err}")
    return out


def lint_one(mapping_path: Path, sample_path: Optional[Path], schema: Schema) -> dict:
    """Lint a single mapping. Returns a dict summarizing the outcome.

    Includes a non-fatal ``warnings`` list: presently this catches the
    mapping_version absence (PLAN.md §3a Bucket C #3) so an unbumped
    edit gets a warning, not a hard fail.
    """
    result = {
        "name": mapping_path.stem,
        "status": "OK",
        "events": 0,
        "classes": [],
        "errors": [],
        "warnings": [],
    }
    if sample_path is None:
        result["status"] = "SKIP"
        result["errors"].append("no sample file found")
        return result

    try:
        config = json.loads(mapping_path.read_text())
    except json.JSONDecodeError as e:
        result["status"] = "FAIL"
        result["errors"].append(f"mapping is not valid JSON: {e}")
        return result

    if not config.get("mapping_version"):
        result["warnings"].append(
            "mapping_version missing — add a semver string (e.g. \"1.0.0\") "
            "to track edits over time"
        )

    lines = sample_path.read_text().splitlines()
    classes_seen: set[str] = set()

    try:
        events = list(apply_stream_with_class(config, lines))
    except Exception as e:
        result["status"] = "FAIL"
        result["errors"].append(f"apply crashed: {e!r}")
        return result

    event_errs: list[tuple[int, str, str]] = []
    for i, (ev, cls) in enumerate(events, 1):
        classes_seen.add(cls)
        errs = validate(ev, cls, schema=schema)
        if errs:
            result["status"] = "FAIL"
            event_errs.append((i, cls, "; ".join(errs)))

    if event_errs:
        result["errors"].extend(_coalesce_event_errors(event_errs))

    result["events"] = len(events)
    result["classes"] = sorted(classes_seen)
    return result


def lint(folder: Path | str = "mappings") -> list[dict]:
    """Lint every mapping in ``folder``. Returns one result dict per mapping."""
    schema = Schema()
    out = []
    for entry in list_mappings(folder):
        sample = Path(entry["sample"]) if entry["sample"] else None
        out.append(lint_one(Path(entry["path"]), sample, schema))
    return out


def main(folder: Optional[str] = None) -> int:
    folder = folder or (sys.argv[1] if len(sys.argv) > 1 else "mappings")
    results = lint(folder)
    if not results:
        print(f"no mappings found in {folder}/")
        return 0

    width = max(len(r["name"]) for r in results)
    flag = {"OK": "✓", "FAIL": "✗", "SKIP": "·"}
    rc = 0
    n_warn = 0
    print(f"linting {len(results)} mapping(s)\n")
    for r in results:
        info = (
            f"{r['events']} event(s) across {len(r['classes'])} class(es)"
            if r["status"] == "OK"
            else (r["errors"][0] if r["errors"] else "")
        )
        print(f"  {flag[r['status']]}  {r['name']:<{width}}  {r['status']:<4}  {info}")
        if r["status"] == "FAIL":
            rc = 1
            for line in r["errors"][1:]:
                print(f"        {line}")
        for w in r.get("warnings", []) or []:
            n_warn += 1
            print(f"        ⚠ {w}")

    print()
    summary = "PASS" if rc == 0 else "FAIL"
    if n_warn:
        summary += f"  ({n_warn} warning(s))"
    print(f"OVERALL: {summary}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
