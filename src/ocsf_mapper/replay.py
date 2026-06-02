"""Replay tool: re-run a new mapping over historical OCSF Parquet/JSONL.

Use case: you've shipped a year's worth of CloudTrail events to Parquet
using ``mappings/cloudtrail.json`` v1.0.0. You edit the mapping to
populate a new field (``cloud.region``, say) and bump it to v1.1.0.
Re-ingesting raw CloudTrail JSON from S3 to backfill the new field
would be slow and expensive — but ``raw_data`` is preserved in every
event by convention, so we can re-apply() directly against the
historical Parquet output.

API
---

  replay_path(in_path, mapping_path, out_path) -> int
      Auto-detect format from ``in_path`` (jsonl / ndjson / parquet)
      and produce one OCSF event per ``raw_data`` line through the
      new mapping. Returns the count written.

  replay_stream(events, config) -> Iterator[dict]
      Iterator-form. Useful when feeding into a sink object directly
      rather than writing a single output file.

CLI
---

  ocsf-mapper replay <in> <mapping> <out>

Both jsonl→jsonl and parquet→parquet round-trips are supported; mixed
in/out formats work too (the writer is chosen from ``out_path``'s
extension via :func:`ocsf_mapper.sinks.infer_kind`).

Constraint: the historical events must carry ``raw_data`` (which all
in-repo mappings populate by default via ``{"raw": true}``). Events
without ``raw_data`` are skipped with a count returned in the report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple

from ocsf_mapper._fastjson import loads as _json_loads
from ocsf_mapper.apply import apply
from ocsf_mapper.sinks import get_sink, infer_kind


# ---------------------------------------------------------------------------
# input readers
# ---------------------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterator[dict]:
    """Stream-decode an NDJSON file."""
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            yield _json_loads(line)


def _iter_parquet(path: Path) -> Iterator[dict]:
    """Stream-decode a Parquet file. Requires pyarrow.

    Handles both a single .parquet file and a directory tree (recurses
    into it, reads every .parquet in lexical order — matches the
    SecurityLakeSink layout).
    """
    try:
        import pyarrow.parquet as pq
    except ImportError as e:  # pragma: no cover - env-specific
        raise ImportError(
            "Parquet input requires pyarrow. Install with: pip install ocsf-mapper[parquet]"
        ) from e

    files: list[Path] = []
    if path.is_dir():
        files = sorted(path.rglob("*.parquet"))
    elif path.is_file():
        files = [path]
    for f in files:
        table = pq.read_table(f)
        for row in table.to_pylist():
            yield row


def iter_events(in_path: Path) -> Iterator[dict]:
    """Auto-detect format and yield events one at a time."""
    if in_path.is_dir() or in_path.suffix == ".parquet":
        return _iter_parquet(in_path)
    return _iter_jsonl(in_path)


# ---------------------------------------------------------------------------
# core replay
# ---------------------------------------------------------------------------


def replay_stream(events: Iterable[dict], config: dict) -> Iterator[Tuple[Optional[dict], str]]:
    """Re-apply ``config`` to every event's ``raw_data``.

    Yields ``(new_event, status)`` per input event. ``status`` is one of:
      - ``"ok"``         remapped successfully
      - ``"no_raw"``     event had no raw_data field
      - ``"no_match"``   apply() returned None (parser/routing didn't match)
    """
    for ev in events:
        raw = ev.get("raw_data")
        if not isinstance(raw, str) or not raw:
            yield None, "no_raw"
            continue
        try:
            new_ev = apply(config, raw)
        except Exception:
            new_ev = None
        if new_ev is None:
            yield None, "no_match"
        else:
            yield new_ev, "ok"


def replay_path(in_path: Path | str, mapping_path: Path | str, out_path: Path | str) -> dict:
    """Read ``in_path``, replay through ``mapping_path``, write to ``out_path``.

    Returns a summary dict::

        {"total": ..., "remapped": ..., "no_raw": ..., "no_match": ...}
    """
    in_p = Path(in_path)
    out_p = Path(out_path)
    config = json.loads(Path(mapping_path).read_text())

    total = remapped = no_raw = no_match = 0
    sink_kind = infer_kind(out_p)
    with get_sink(sink_kind, out_p) as sink:
        for new_ev, status in replay_stream(iter_events(in_p), config):
            total += 1
            if status == "ok" and new_ev is not None:
                sink.write_one(new_ev)
                remapped += 1
            elif status == "no_raw":
                no_raw += 1
            else:
                no_match += 1

    return {
        "total":    total,
        "remapped": remapped,
        "no_raw":   no_raw,
        "no_match": no_match,
    }


def render_summary(result: dict) -> str:
    return (
        f"replayed {result['total']:,} event(s)\n"
        f"  ✓ remapped: {result['remapped']:,}\n"
        f"  · no raw_data: {result['no_raw']:,}\n"
        f"  · no match: {result['no_match']:,}\n"
    )


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 3:
        print(
            "usage: ocsf-mapper replay <input> <mapping.json> <output>",
            file=sys.stderr,
        )
        return 2
    in_path, mapping_path, out_path = argv[0], argv[1], argv[2]
    result = replay_path(in_path, mapping_path, out_path)
    print(render_summary(result), end="", file=sys.stderr)
    print(f"  → {out_path}", file=sys.stderr)
    return 0 if result["remapped"] > 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
