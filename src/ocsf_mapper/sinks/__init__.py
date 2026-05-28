"""Output sinks for OCSF events.

A sink owns a single output destination. The minimum contract is the :class:`Sink`
Protocol in ``base.py``::

    class Sink(Protocol):
        def write_one(self, event: dict) -> None: ...
        def write_many(self, events: Iterable[dict]) -> int: ...
        def close(self) -> None: ...

Sinks are context managers — use ``with JsonlSink(path) as s:`` to ensure
``close()`` is called.

Use :func:`get_sink` to look up a sink by name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ocsf_mapper.sinks.base import Sink
from ocsf_mapper.sinks.jsonl import JsonlSink
from ocsf_mapper.sinks.csv import CsvSink
from ocsf_mapper.sinks.stdout import StdoutSink

__all__ = ["Sink", "JsonlSink", "CsvSink", "StdoutSink", "get_sink", "infer_kind"]


def get_sink(kind: str, path: Optional[Path | str] = None, **kwargs) -> Sink:
    """Return a concrete sink for ``kind`` (``"jsonl"``, ``"csv"``, ``"parquet"``, ``"stdout"``).

    ``ParquetSink`` is imported lazily so the optional ``pyarrow`` dep is only
    required when actually requested.
    """
    kind = kind.lower()
    if kind == "stdout":
        return StdoutSink()
    if path is None:
        raise ValueError(f"sink kind {kind!r} requires a path")
    if kind in ("jsonl", "ndjson", "json"):
        return JsonlSink(path)
    if kind == "csv":
        return CsvSink(path, **kwargs)
    if kind == "parquet":
        from ocsf_mapper.sinks.parquet import ParquetSink  # lazy: optional dep
        return ParquetSink(path, **kwargs)
    raise ValueError(
        f"unknown sink kind: {kind!r} (expected jsonl/csv/parquet/stdout)"
    )


def infer_kind(path: Optional[Path | str]) -> str:
    """Guess the sink kind from a path's extension. Defaults to ``"jsonl"``."""
    if path is None or str(path) == "-":
        return "stdout"
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in ("jsonl", "ndjson", "json"):
        return "jsonl"
    if ext == "csv":
        return "csv"
    if ext == "parquet":
        return "parquet"
    return "jsonl"
