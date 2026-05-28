"""Detect breaking OCSF schema changes before they bite.

When the ``ocsf-schema`` submodule is bumped (or any time you want to
check), this module compares the current schema against an arbitrary
older git ref of the same repo and reports per-class changes to:

* required attribute set (added / removed)
* ``at_least_one`` constraints (added / removed)
* ``activity_id`` enum (added / removed values)

It also joins those changes against the on-disk ``mappings/`` so you can
see exactly which existing mappings will silently break under the new
schema. Typical flow::

    # After: git submodule update --remote ocsf-schema
    ocsf-mapper schema-diff
    # → 'authentication +metadata.required → 5 of 5 mappings populate it: OK'
    # → 'http_activity +session_id required → 0 of 3 mappings populate it: BREAKS'

The diff uses ``git show <ref>:<path>`` against the submodule's own git
history; no extra checkout is performed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from ocsf_mapper.registry import list_mappings
from ocsf_mapper.schema import Schema


def _attrs_by_requirement(cls: dict, level: str) -> set[str]:
    return {
        n for n, sp in (cls.get("attributes") or {}).items()
        if isinstance(sp, dict)
        and not n.startswith("$")
        and sp.get("requirement") == level
    }


def _at_least_one(cls: dict) -> list[tuple[str, ...]]:
    """Return ``at_least_one`` constraints as a list of tuples (order-stable)."""
    cstr = cls.get("constraints") or {}
    out = []
    val = cstr.get("at_least_one")
    if isinstance(val, list):
        # Single list of attrs.
        out.append(tuple(sorted(val)))
    elif isinstance(val, list) and val and isinstance(val[0], list):
        for group in val:
            out.append(tuple(sorted(group)))
    return out


def _activity_enum(cls: dict) -> set[str]:
    enum = (cls.get("attributes") or {}).get("activity_id", {}).get("enum") or {}
    return set(str(k) for k in enum.keys())


def load_class_at_ref(
    class_name: str,
    schema_root: Path,
    ref: str,
) -> Optional[dict]:
    """Load a single class definition from an older git ref of the schema repo.

    Returns ``None`` if the file didn't exist at that ref. Does not perform
    the ``extends`` chain merge — schema-bump comparison cares about each
    file's own declarations, not inherited shape.
    """
    candidates = list((schema_root / "events").rglob(f"{class_name}.json"))
    if not candidates:
        return None
    rel = candidates[0].relative_to(schema_root).as_posix()
    try:
        raw = subprocess.check_output(
            ["git", "show", f"{ref}:{rel}"],
            cwd=schema_root,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def diff_against(
    ref: str = "HEAD~1",
    schema: Optional[Schema] = None,
) -> dict:
    """Per-class diff of the schema vs ``ref`` (a git ref in the submodule).

    Returns ``{class_name: {added_required, removed_required, added_constraints,
    removed_constraints, added_activity, removed_activity, new_class, removed_class}}``.
    Classes with no changes are omitted from the result.
    """
    schema = schema or Schema()
    out: dict = {}

    # Inspect each class file directly (not via load_class) so we compare
    # per-file declarations rather than the merged extends chain.
    for p in sorted((schema.root / "events").rglob("*.json")):
        if p.name == "base_event.json":
            continue
        try:
            current = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        name = current.get("name")
        if not name:
            continue
        old = load_class_at_ref(name, schema.root, ref)

        cur_req = _attrs_by_requirement(current, "required")
        cur_cstr = set(_at_least_one(current))
        cur_act = _activity_enum(current)

        if old is None:
            out[name] = {
                "new_class": True,
                "added_required": sorted(cur_req),
                "removed_required": [],
                "added_constraints": sorted(cur_cstr),
                "removed_constraints": [],
                "added_activity": sorted(cur_act),
                "removed_activity": [],
            }
            continue

        old_req = _attrs_by_requirement(old, "required")
        old_cstr = set(_at_least_one(old))
        old_act = _activity_enum(old)

        added_req = sorted(cur_req - old_req)
        removed_req = sorted(old_req - cur_req)
        added_cstr = sorted(cur_cstr - old_cstr)
        removed_cstr = sorted(old_cstr - cur_cstr)
        added_act = sorted(cur_act - old_act)
        removed_act = sorted(old_act - cur_act)

        if any((added_req, removed_req, added_cstr, removed_cstr,
                added_act, removed_act)):
            out[name] = {
                "new_class": False,
                "added_required": added_req,
                "removed_required": removed_req,
                "added_constraints": added_cstr,
                "removed_constraints": removed_cstr,
                "added_activity": added_act,
                "removed_activity": removed_act,
            }

    return out


def affected_mappings(
    diff: dict,
    mappings_dir: Path | str = "mappings",
) -> dict:
    """Join a diff against ``mappings_dir``: which mappings declare each
    changed class, and which of those populate the newly-required attrs.

    Returns ``{class_name: {"mappings": [{"name", "populated", "missing"}], ...}}``.
    """
    out: dict = {}
    rows = list_mappings(mappings_dir)
    by_class: dict[str, list[dict]] = {}
    for r in rows:
        try:
            cfg = json.loads(Path(r["path"]).read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for cls_name, block in (cfg.get("classes") or {}).items():
            target_prefixes = {t.split(".", 1)[0] for t in (block.get("mapping") or {})}
            by_class.setdefault(cls_name, []).append({
                "name": r["name"],
                "prefixes": target_prefixes,
            })

    for cls_name, d in diff.items():
        mappings_info = []
        for m in by_class.get(cls_name, []):
            missing = [a for a in d.get("added_required", []) if a not in m["prefixes"]]
            populated = [a for a in d.get("added_required", []) if a in m["prefixes"]]
            mappings_info.append({
                "name": m["name"],
                "populated": populated,
                "missing": missing,
            })
        out[cls_name] = {**d, "mappings": mappings_info}
    return out


def render_report(joined: dict) -> str:
    """Human-readable report ready for stdout."""
    if not joined:
        return "No schema-affecting changes.\n"
    lines: list[str] = []
    for cls_name, info in sorted(joined.items()):
        head = f"class: {cls_name}"
        if info.get("new_class"):
            head += "  (NEW)"
        lines.append(head)
        if info["added_required"]:
            lines.append(f"  + required: {info['added_required']}")
            ms = info.get("mappings") or []
            if ms:
                breakers = [m["name"] for m in ms if m["missing"]]
                clean    = [m["name"] for m in ms if not m["missing"]]
                if breakers:
                    lines.append(f"    ✗ mappings missing the new attr(s): {', '.join(breakers)}")
                if clean:
                    lines.append(f"    ✓ mappings already populating: {', '.join(clean)}")
            else:
                lines.append("    · no mapping declares this class")
        if info["removed_required"]:
            lines.append(f"  - required: {info['removed_required']}  (less restrictive — safe)")
        if info["added_constraints"]:
            lines.append(f"  + at_least_one: {info['added_constraints']}")
        if info["removed_constraints"]:
            lines.append(f"  - at_least_one: {info['removed_constraints']}")
        if info["added_activity"]:
            lines.append(f"  + activity_id enum: {info['added_activity']}")
        if info["removed_activity"]:
            lines.append(f"  - activity_id enum: {info['removed_activity']}  (potentially breaking)")
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry: ``python -m ocsf_mapper.schema_diff [<ref>] [<mappings>]``."""
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    ref = argv[0] if argv else "HEAD~1"
    mappings_dir = argv[1] if len(argv) > 1 else "mappings"
    try:
        diff = diff_against(ref)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"error: git failed in submodule: {e}", file=sys.stderr)
        return 1
    joined = affected_mappings(diff, mappings_dir)
    print(render_report(joined), end="")
    # Exit non-zero iff at least one mapping is broken by newly-required attrs.
    breakers = sum(
        1 for info in joined.values()
        for m in info.get("mappings", []) if m["missing"]
    )
    return 1 if breakers else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
