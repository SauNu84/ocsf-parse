"""Mapping engine — turns raw log lines into OCSF events using a JSON DSL config.

This module owns the orchestration:

  raw line ── parse_record ──> record dict
                                  │
                                  ▼
                            pick_class (routing)
                                  │
                                  ▼
                            map_record (apply ops per target)
                                  │
                                  ▼
                                prune
                                  │
                                  ▼
                              OCSF event

Op execution is delegated to :mod:`ocsf_mapper.ops`. The public surface is
:func:`apply` (single line) and :func:`apply_stream` (iterator). Both also
have ``_with_class`` variants that additionally return the chosen class
name — used by the linter and any future tooling that needs to validate
per-class.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Iterable, Iterator, Mapping, Optional, Tuple

from ocsf_mapper.ops import apply_op, resolve_expr, set_path


# ---------------------------------------------------------------------------
# parsing — raw line to record
# ---------------------------------------------------------------------------


@lru_cache(maxsize=128)
def _compile_regex(pattern: str) -> re.Pattern:
    """Cache compiled regex patterns across calls.

    parse_record is on the hot path; re.match() on a string-form pattern
    re-compiles each call. With this cache the compile cost is paid once
    per unique parser, which matters at 10⁶+ events/run.
    """
    return re.compile(pattern)


def parse_record(raw_line: str, parser_spec: Any) -> Optional[dict]:
    """Parse a raw line into a record dict.

    Returns ``None`` for lines that don't match the configured parser; the
    caller is expected to skip them.
    """
    if parser_spec == "json":
        rec = json.loads(raw_line)
        rec["__raw__"] = raw_line.rstrip("\n")
        return rec
    if isinstance(parser_spec, dict) and "regex" in parser_spec:
        m = _compile_regex(parser_spec["regex"]).match(raw_line.rstrip("\n"))
        if not m:
            return None
        groups = m.groupdict()
        rec: dict = {"__groups__": groups, "__raw__": raw_line.rstrip("\n")}
        # Also expose groups at top level so JSON-path ops can address them.
        rec.update(groups)
        return rec
    raise ValueError(f"unknown parser: {parser_spec!r}")


# ---------------------------------------------------------------------------
# routing — record to class name
# ---------------------------------------------------------------------------


def pick_class(record: Mapping[str, Any], routing: Optional[dict], classes: dict) -> str:
    """Pick which OCSF class to apply for this record.

    If ``routing`` is absent or has no matching rule, falls back to the first
    class declared in ``classes`` (or ``routing.default_class`` if set).
    """
    if not routing:
        return next(iter(classes))
    field_val = resolve_expr(routing["field"], record)
    for rule in routing["rules"]:
        if rule.get("default"):
            return rule["class"]
        matches = rule.get("matches", [])
        if rule.get("prefix"):
            if any(str(field_val or "").startswith(m) for m in matches):
                return rule["class"]
        else:
            if str(field_val) in matches:
                return rule["class"]
    return routing.get("default_class") or next(iter(classes))


# ---------------------------------------------------------------------------
# mapping — record to OCSF event
# ---------------------------------------------------------------------------


def prune(obj: Any) -> Any:
    """Recursively drop ``None`` values and empty dicts/lists.

    Mapping configs intentionally over-declare targets; pruning keeps the
    output clean for the validator (and matches what real OCSF events look
    like — missing optional fields are simply absent).
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            pv = prune(v)
            if pv not in (None, {}, []):
                out[k] = pv
        return out
    if isinstance(obj, list):
        return [prune(x) for x in obj if x is not None]
    return obj


def map_record(record: Mapping[str, Any], class_block: dict) -> dict:
    """Run all ops in ``class_block['mapping']`` against ``record``, build an OCSF event."""
    event: dict = {}
    already_set: dict = {}
    for target, op in class_block["mapping"].items():
        val = apply_op(op, record, already_set)
        set_path(event, target, val)
        # Top-level scalar targets are exposed to subsequent `expr` ops.
        if "." not in target and isinstance(val, (int, float, str)):
            already_set[target] = val
    return prune(event)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def apply(config: dict, raw_line: str) -> Optional[dict]:
    """Map a single raw line to one OCSF event, or ``None`` if unparseable."""
    result = _apply_with_class(config, raw_line)
    return None if result is None else result[0]


def apply_with_class(config: dict, raw_line: str) -> Optional[Tuple[dict, str]]:
    """Same as :func:`apply` but also returns the chosen OCSF class name."""
    return _apply_with_class(config, raw_line)


def apply_stream(config: dict, lines: Iterable[str]) -> Iterator[dict]:
    """Map a stream of raw lines. Empty / unparseable lines are skipped."""
    for line in lines:
        if not line.strip():
            continue
        ev = apply(config, line)
        if ev is not None:
            yield ev


def apply_stream_with_class(
    config: dict, lines: Iterable[str]
) -> Iterator[Tuple[dict, str]]:
    """Like :func:`apply_stream` but yields ``(event, class_name)`` pairs."""
    for line in lines:
        if not line.strip():
            continue
        r = _apply_with_class(config, line)
        if r is not None:
            yield r


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _apply_with_class(config: dict, raw_line: str) -> Optional[Tuple[dict, str]]:
    rec = parse_record(raw_line, config["parser"])
    if rec is None:
        return None
    cls = pick_class(rec, config.get("routing"), config["classes"])
    block = config["classes"][cls]
    event = map_record(rec, block)
    return event, cls
