"""Parquet sink (optional dep: ``pyarrow``).

Single-file Parquet output. For partitioned Security-Lake-style layouts
see :class:`ocsf_mapper.sinks.security_lake.SecurityLakeSink`.

Install with::

    pip install ocsf-mapper[parquet]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

from ocsf_mapper.sinks.base import _SinkBase


class ParquetSink(_SinkBase):
    """Buffered Parquet sink — flushes on :meth:`close`.

    Pass ``schema=`` (a ``pyarrow.Schema``) to skip per-flush type inference,
    which matters when the event payload is large or the inferred schema
    drifts between flushes. Use
    :func:`ocsf_mapper.sinks.security_lake.infer_schema_from` to build one
    from a representative sample event.

    Buffers all rows in memory; for very large inputs use
    :class:`~ocsf_mapper.sinks.security_lake.SecurityLakeSink` which flushes
    by partition.
    """

    def __init__(self, path: Path | str, schema: Optional[Any] = None) -> None:
        try:
            import pyarrow  # noqa: F401  # ensure dep is present
            import pyarrow.parquet  # noqa: F401
        except ImportError as e:  # pragma: no cover - exercised when pyarrow absent
            raise ImportError(
                "ParquetSink requires pyarrow. Install with: pip install ocsf-mapper[parquet]"
            ) from e
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.schema = schema
        self._rows: list[dict] = []

    def write_one(self, event: dict) -> None:
        self._rows.append(event)

    def write_many(self, events: Iterable[dict]) -> int:
        n = 0
        for ev in events:
            self._rows.append(ev)
            n += 1
        return n

    def close(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        if not self._rows:
            table = pa.Table.from_pylist([], schema=self.schema)
        else:
            table = pa.Table.from_pylist(self._rows, schema=self.schema)
        pq.write_table(table, self.path)
