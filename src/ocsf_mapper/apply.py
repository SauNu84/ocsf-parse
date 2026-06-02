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

import re
from functools import lru_cache
from typing import Any, Iterable, Iterator, Mapping, Optional, Tuple

from ocsf_mapper._fastjson import loads as _json_loads
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

    Supported parser kinds:

      * ``"json"`` — one JSON object per line.
      * ``{"regex": "<pattern>", "groups": [...]}`` — named regex groups.
      * ``"cef"`` — ArcSight CEF format. Produces ``{cef_version,
        device_vendor, device_product, device_version, signature_id,
        name, severity, ext: {...}}`` with the ``key=value`` extension
        parsed into ``ext``.
      * ``"leef"`` — IBM LEEF format. Produces ``{leef_version, vendor,
        product, version, event_id, ext: {...}}``.

    The ``"cef"`` and ``"leef"`` forms also expose the extension keys at
    the top level so DSL paths like ``$.src`` work without going through
    ``$.ext.src``.
    """
    if parser_spec == "json":
        rec = _json_loads(raw_line)
        rec["__raw__"] = raw_line.rstrip("\n")
        return rec
    if parser_spec == "cef":
        return _parse_cef(raw_line)
    if parser_spec == "leef":
        return _parse_leef(raw_line)
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
# CEF / LEEF parsers (vendor-neutral SIEM transports)
# ---------------------------------------------------------------------------


def _parse_cef(raw_line: str) -> Optional[dict]:
    """Parse an ArcSight CEF line.

    Format::

        CEF:Version|Device Vendor|Device Product|Device Version|Signature ID|Name|Severity|Extension

    Eight pipe-separated fields after the ``CEF:`` prefix. The
    extension is a free-form ``key=value`` blob — keys are
    space-delimited, values run until the next ``<space><known-key>=``.
    """
    line = raw_line.rstrip("\n")
    if not line.startswith("CEF:"):
        return None
    body = line[4:]
    parts = _split_cef_header(body, n_fields=8)
    if parts is None or len(parts) < 8:
        return None
    cef_version, vendor, product, version, sig_id, name, severity, ext_blob = parts
    ext = _parse_cef_extension(ext_blob)
    rec: dict = {
        "cef_version":    cef_version,
        "device_vendor":  vendor,
        "device_product": product,
        "device_version": version,
        "signature_id":   sig_id,
        "name":           name,
        "severity":       severity,
        "ext":            ext,
        "__raw__":        line,
    }
    # Flatten extension keys to the top level so $.<key> works directly.
    for k, v in ext.items():
        if k not in rec:
            rec[k] = v
    return rec


def _split_cef_header(body: str, n_fields: int) -> Optional[list[str]]:
    """Split a CEF body on unescaped ``|``. Honours ``\\|`` and ``\\\\`` escapes."""
    fields: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(body) and len(fields) < n_fields - 1:
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in ("|", "\\", "="):
                buf.append(nxt)
                i += 2
                continue
        if c == "|":
            fields.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    # Everything remaining is the final field (severity + extension).
    fields.append("".join(buf) + body[i:])
    return fields


def _parse_cef_extension(blob: str) -> dict[str, str]:
    """Parse a CEF ``key=value key2=value2`` extension blob.

    Values can contain spaces — we look for the next ``<space>word=`` to
    delimit. Honours ``\\=`` and ``\\\\`` escapes inside values.
    """
    if not blob:
        return {}
    # Find all "key=" positions in the string (start, or preceded by space).
    key_pat = re.compile(r"(?:^|(?<=\s))([A-Za-z_][\w.]*?)=")
    matches = list(key_pat.finditer(blob))
    out: dict[str, str] = {}
    for idx, m in enumerate(matches):
        key = m.group(1)
        val_start = m.end()
        val_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(blob)
        raw_val = blob[val_start:val_end].rstrip()
        # Unescape \= \\ \|
        val = raw_val.replace("\\\\", "\x00").replace("\\=", "=").replace("\\|", "|").replace("\x00", "\\")
        out[key] = val
    return out


def _parse_leef(raw_line: str) -> Optional[dict]:
    """Parse an IBM LEEF line.

    LEEF 1.0::

        LEEF:1.0|Vendor|Product|Version|EventID|<tab-separated key=value>

    LEEF 2.0::

        LEEF:2.0|Vendor|Product|Version|EventID|<delim>|<key=value...>

    where ``<delim>`` is the character used to separate extension pairs
    (commonly tab ``\\t``, ``|``, ``\\x09``, or a single character).

    The record shape mirrors :func:`_parse_cef`.
    """
    line = raw_line.rstrip("\n")
    if not line.startswith("LEEF:"):
        return None
    body = line[5:]
    # Peek at the version to decide how many pipes to split on. The
    # extension is *one* trailing field, so use ``maxsplit`` rather than
    # a plain ``split("|")`` (which would shred any pipes that appear
    # inside extension values).
    head_only = body.split("|", 1)
    if not head_only:
        return None
    leef_version = head_only[0]
    if leef_version.startswith("2"):
        parts = body.split("|", 6)         # 7 fields: 6 pipes
        if len(parts) < 7:
            return None
        _, vendor, product, version, event_id, delim_field, ext_blob = parts
        delim = _normalise_leef_delim(delim_field)
    else:
        parts = body.split("|", 5)         # 6 fields: 5 pipes
        if len(parts) < 6:
            return None
        _, vendor, product, version, event_id, ext_blob = parts
        delim = "\t"
    ext = _parse_leef_extension(ext_blob, delim)
    rec: dict = {
        "leef_version": leef_version,
        "vendor":       vendor,
        "product":      product,
        "version":      version,
        "event_id":     event_id,
        "ext":          ext,
        "__raw__":      line,
    }
    for k, v in ext.items():
        if k not in rec:
            rec[k] = v
    return rec


def _normalise_leef_delim(delim_field: str) -> str:
    """Map common LEEF 2.0 delimiter encodings to a literal character."""
    d = delim_field.strip()
    if d in ("\\t", "x09", "0x09", "9"):
        return "\t"
    if not d:
        return "\t"
    return d[0]


def _parse_leef_extension(blob: str, delim: str) -> dict[str, str]:
    """Split a LEEF extension blob on ``delim``, parse k=v pairs."""
    if not blob:
        return {}
    out: dict[str, str] = {}
    for pair in blob.split(delim):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        if k:
            out[k.strip()] = v
    return out


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
