"""Side-by-side diff of two mapping config files.

Use this when a vendor ships multiple log variants (palo_alto traffic vs
url_filtering; cloudtrail vs cloudtrail_lake) and you want to see what
changed without eyeballing 200-line JSON files.

The diff operates at three levels of granularity per OCSF class:

* **Routing** — fields, rules, default class
* **Mapping targets** — which OCSF attributes the mapping populates
* **Op detail** — for shared targets, what kind of op + which lookup
  table keys / parser regex / etc.

Output shapes:

* :func:`diff_mappings` — structured dict, easy to render any way
* :func:`render_text_report` — human-readable plain text
* :func:`main` — CLI entry: ``python -m ocsf_mapper.mapping_diff a.json b.json``

Both inputs can be paths or already-parsed dicts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable, Optional, Union


_MappingLike = Union[dict, str, Path]


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------


def _as_dict(arg: _MappingLike) -> dict:
    if isinstance(arg, dict):
        return arg
    return json.loads(Path(arg).read_text())


def _op_kind(op: Any) -> str:
    """The leading keyword of an op spec (`const`, `path`, `lookup`, ...).

    Falls back to ``"unknown"`` for things we don't recognise so the diff
    keeps working on hand-edited mappings that contain typos.
    """
    if not isinstance(op, dict):
        return "literal"
    for k in ("const", "path", "group", "raw", "lookup", "time",
              "range", "int", "bool", "expr", "for_each"):
        if k in op:
            return k
    return "unknown"


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def diff_mappings(a: _MappingLike, b: _MappingLike) -> dict:
    """Compute a structured diff between two mappings.

    Returns::

        {
          "header": {                # cross-mapping metadata changes
            "source_name":  {"a": ..., "b": ...},
            "vendor":       ..., "priority": ..., "display_name": ...,
            "parser":       "same" | "different",
          },
          "routing":     {"a_only": [...], "b_only": [...], "shared": [...],
                          "rules_changed": bool},
          "classes": {
            "a_only":  [<class>...],
            "b_only":  [<class>...],
            "shared":  {<class>: {
                "added":   [<target>, ...],    # populated only by B
                "removed": [<target>, ...],    # populated only by A
                "common":  [<target>, ...],
                "op_changed": [
                    {"target": ..., "a_kind": ..., "b_kind": ...,
                     "a_detail": <op>, "b_detail": <op>},
                    ...
                ],
            }},
          },
        }
    """
    A, B = _as_dict(a), _as_dict(b)

    header_keys = ("source_name", "display_name", "vendor", "priority")
    header_diff: dict[str, dict[str, Any]] = {}
    for k in header_keys:
        if A.get(k) != B.get(k):
            header_diff[k] = {"a": A.get(k), "b": B.get(k)}
    parser_match = A.get("parser") == B.get("parser")
    header_diff["parser"] = "same" if parser_match else "different"

    routing_diff = _diff_routing(A.get("routing"), B.get("routing"))

    cls_a = set((A.get("classes") or {}).keys())
    cls_b = set((B.get("classes") or {}).keys())
    shared_cls = sorted(cls_a & cls_b)

    classes_block: dict[str, Any] = {
        "a_only":  sorted(cls_a - cls_b),
        "b_only":  sorted(cls_b - cls_a),
        "shared":  {},
    }
    for cls in shared_cls:
        classes_block["shared"][cls] = _diff_class_block(
            (A["classes"][cls] or {}),
            (B["classes"][cls] or {}),
        )

    return {
        "header":  header_diff,
        "routing": routing_diff,
        "classes": classes_block,
    }


def _diff_routing(a: Any, b: Any) -> dict:
    a = a or {}
    b = b or {}
    diff = {
        "field_changed": a.get("field") != b.get("field"),
        "a_field": a.get("field"),
        "b_field": b.get("field"),
        "rules_changed": (a.get("rules") or []) != (b.get("rules") or []),
        "n_rules_a": len(a.get("rules") or []),
        "n_rules_b": len(b.get("rules") or []),
    }
    return diff


def _diff_class_block(a: dict, b: dict) -> dict:
    a_map = a.get("mapping") or {}
    b_map = b.get("mapping") or {}
    a_targets = set(a_map.keys())
    b_targets = set(b_map.keys())
    common = sorted(a_targets & b_targets)
    op_changed: list[dict] = []
    for t in common:
        ka, kb = _op_kind(a_map[t]), _op_kind(b_map[t])
        if ka != kb or a_map[t] != b_map[t]:
            op_changed.append({
                "target": t,
                "a_kind": ka, "b_kind": kb,
                "a_detail": a_map[t], "b_detail": b_map[t],
            })
    return {
        "added":   sorted(b_targets - a_targets),
        "removed": sorted(a_targets - b_targets),
        "common":  common,
        "op_changed": op_changed,
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_text_report(diff: dict, names: tuple[str, str] = ("A", "B")) -> str:
    """Plain-text report of :func:`diff_mappings` output."""
    a_name, b_name = names
    lines: list[str] = []
    lines.append(f"# Mapping diff: {a_name} → {b_name}")
    lines.append("")

    # Header
    header = diff["header"]
    metadata_changes = [k for k in ("source_name", "display_name", "vendor", "priority")
                         if k in header]
    if metadata_changes:
        lines.append("## Metadata")
        for k in metadata_changes:
            lines.append(f"  - {k}: {header[k]['a']!r} → {header[k]['b']!r}")
        lines.append("")
    if header.get("parser") == "different":
        lines.append("## Parser")
        lines.append("  - parser differs between the two mappings")
        lines.append("")

    # Routing
    routing = diff["routing"]
    if routing["field_changed"] or routing["rules_changed"]:
        lines.append("## Routing")
        if routing["field_changed"]:
            lines.append(f"  - field: {routing['a_field']!r} → {routing['b_field']!r}")
        if routing["rules_changed"]:
            lines.append(f"  - {routing['n_rules_a']} rule(s) → {routing['n_rules_b']} rule(s)")
        lines.append("")

    # Classes
    cls = diff["classes"]
    if cls["a_only"] or cls["b_only"]:
        lines.append("## Classes (set diff)")
        if cls["a_only"]:
            lines.append(f"  - only in {a_name}: {', '.join(cls['a_only'])}")
        if cls["b_only"]:
            lines.append(f"  - only in {b_name}: {', '.join(cls['b_only'])}")
        lines.append("")

    for cls_name in sorted(cls["shared"]):
        info = cls["shared"][cls_name]
        if not (info["added"] or info["removed"] or info["op_changed"]):
            continue
        lines.append(f"## Class: {cls_name}")
        if info["removed"]:
            lines.append(f"  - removed targets ({len(info['removed'])}):")
            for t in info["removed"]:
                lines.append(f"    - {t}")
        if info["added"]:
            lines.append(f"  - added targets ({len(info['added'])}):")
            for t in info["added"]:
                lines.append(f"    + {t}")
        if info["op_changed"]:
            lines.append(f"  - op changed ({len(info['op_changed'])}):")
            for ch in info["op_changed"]:
                if ch["a_kind"] != ch["b_kind"]:
                    lines.append(f"    ~ {ch['target']}: {ch['a_kind']} → {ch['b_kind']}")
                else:
                    lines.append(f"    ~ {ch['target']}: {ch['a_kind']} op body changed")
        lines.append("")

    if not _has_any_change(diff):
        lines.append("_(no differences detected)_")
        lines.append("")
    return "\n".join(lines)


def _has_any_change(diff: dict) -> bool:
    header_metadata = any(
        k in diff["header"]
        for k in ("source_name", "display_name", "vendor", "priority")
    )
    routing_changed = (
        diff["routing"]["field_changed"] or diff["routing"]["rules_changed"]
    )
    class_set_changed = bool(diff["classes"]["a_only"] or diff["classes"]["b_only"])
    body_changed = any(
        v["added"] or v["removed"] or v["op_changed"]
        for v in diff["classes"]["shared"].values()
    )
    parser_changed = diff["header"].get("parser") == "different"
    return any([header_metadata, routing_changed, class_set_changed,
                body_changed, parser_changed])


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(argv: Optional[Iterable[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print(
            "usage: ocsf-mapper diff <a.json> <b.json> [--json]",
            file=sys.stderr,
        )
        return 2
    a, b = argv[0], argv[1]
    as_json = "--json" in argv[2:]
    diff = diff_mappings(a, b)
    if as_json:
        print(json.dumps(diff, indent=2))
        return 0
    print(render_text_report(diff, names=(Path(a).stem, Path(b).stem)), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
