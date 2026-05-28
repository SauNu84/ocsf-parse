"""DSL op kinds for ocsf_mapper.

Each op is a small JSON object describing how to derive one OCSF attribute value
from a parsed source record. The set of supported op kinds is intentionally
narrow — the DSL caps at ~12 kinds (see PLAN.md §5). Anything more dynamic
should be raised as an issue, not added as a new op.

Op dispatch lives in :func:`apply_op`. Helpers (:func:`get_path`,
:func:`set_path`, :func:`resolve_expr`) are reused by the orchestration layer
in ``apply.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


_TRUTHY = frozenset({"true", "yes", "y", "1", "success", "allow"})


def get_path(obj: Any, path: str | None) -> Any:
    """Resolve a JSON path like ``$.a.b.0.c`` against nested dicts and lists.

    Integer-looking path parts index into lists. Missing keys return ``None``
    without raising.
    """
    if path is None:
        return None
    parts = path.lstrip("$").lstrip(".").split(".")
    cur: Any = obj
    for p in parts:
        if p == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and p.isdigit():
            idx = int(p)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def set_path(obj: dict, dotted: str, value: Any) -> None:
    """Set ``obj['a']['b']['c'] = value`` from a dotted path.

    Creates intermediate dicts as needed. ``None`` values are skipped so callers
    don't have to filter them. List indices are not supported on the target side
    (mapping targets are always object keys).
    """
    if value is None:
        return
    parts = dotted.split(".")
    cur: Any = obj
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def resolve_expr(expr: Any, record: Mapping[str, Any]) -> Any:
    """Resolve an op's input expression.

    Strings starting with ``$`` are JSON-paths against the record; anything else
    is a literal.
    """
    if isinstance(expr, str) and expr.startswith("$"):
        return get_path(record, expr)
    return expr


def apply_op(
    op: Mapping[str, Any],
    record: Mapping[str, Any],
    already_set: Mapping[str, Any] | None = None,
) -> Any:
    """Execute one op spec and return the resulting value (or ``None`` to skip).

    ``record`` is the parsed source event. The orchestration layer is expected
    to inject ``__raw__`` (raw line) and ``__groups__`` (regex captures) into
    the record before calling this — see :func:`ocsf_mapper.apply.parse_record`.

    ``already_set`` is the partially-built OCSF event's flat scalar targets,
    used by the ``expr`` op for cross-field arithmetic.
    """
    already_set = already_set or {}

    if "const" in op:
        return op["const"]

    if "path" in op:
        return get_path(record, op["path"])

    if "group" in op:
        return record.get("__groups__", {}).get(op["group"])

    if "raw" in op and op["raw"]:
        return record.get("__raw__")

    if "lookup" in op:
        val = resolve_expr(op["lookup"], record)
        if val is None:
            return op["if_null"] if "if_null" in op else op.get("default")
        table = op["table"]
        if op.get("prefix_match"):
            for k, v in table.items():
                if str(val).startswith(k):
                    return v
            return op.get("default")
        return table.get(str(val), op.get("default"))

    if "time" in op:
        v = resolve_expr(op["time"], record)
        if v is None:
            return None
        fmt = op.get("format", "iso8601")
        if fmt == "iso8601":
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        elif fmt == "epoch_ms":
            return int(v)
        elif fmt == "epoch_s":
            return int(v) * 1000
        elif fmt.startswith("strptime:"):
            dt = datetime.strptime(str(v), fmt.split("strptime:", 1)[1])
        else:
            raise ValueError(f"unknown time format: {fmt}")
        return int(dt.astimezone(timezone.utc).timestamp() * 1000)

    if "range" in op:
        v = resolve_expr(op["range"], record)
        if v is None:
            return op.get("default")
        for low, high, value in op["ranges"]:
            if low <= int(v) <= high:
                return value
        return op.get("default")

    if "int" in op:
        v = resolve_expr(op["int"], record)
        return None if v is None else int(v)

    if "bool" in op:
        v = resolve_expr(op["bool"], record)
        if v is None:
            return None
        return str(v).strip().lower() in _TRUTHY

    if "expr" in op:
        # Sandboxed arithmetic over already-set scalars. Mappings come from the
        # local filesystem (PLAN.md §6 #1), so the risk surface is the same as
        # any other code the user runs locally — we still strip builtins.
        try:
            return eval(op["expr"], {"__builtins__": {}}, dict(already_set))
        except Exception:
            return None

    if "for_each" in op:
        # Iterate over an array in the record and build one sub-object per item.
        # The iteration variable is bound under the name in `as` (default: "item")
        # and is reachable from nested ops via `$.<as>...`.
        items = resolve_expr(op["for_each"], record)
        if not isinstance(items, list):
            return None
        as_name = op.get("as", "item")
        sub_map = op.get("map", {})
        out: list = []
        for item in items:
            sub_record = {**dict(record), as_name: item}
            sub_event: dict = {}
            sub_already_set: dict = {}
            for target, sub_op in sub_map.items():
                val = apply_op(sub_op, sub_record, sub_already_set)
                set_path(sub_event, target, val)
                if "." not in target and isinstance(val, (int, float, str)):
                    sub_already_set[target] = val
            out.append(sub_event)
        return out

    raise ValueError(f"unknown op: {op!r}")
