"""Parquet sink (optional dep: ``pyarrow``).

Direct compatibility with AWS Security Lake's expected format. Install with::

    pip install ocsf-mapper[parquet]
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ocsf_mapper.sinks.base import _SinkBase


class ParquetSink(_SinkBase):
    """Buffered Parquet sink — flushes on :meth:`close`.

    Buffering means memory grows with event count; for very large batches use
    JsonlSink and convert downstream.
    """

    def __init__(self, path: Path | str) -> None:
        try:
            import pyarrow  # noqa: F401  # ensure dep is present
            import pyarrow.parquet  # noqa: F401
        except ImportError as e:  # pragma: no cover - exercised when pyarrow absent
            raise ImportError(
                "ParquetSink requires pyarrow. Install with: pip install ocsf-mapper[parquet]"
            ) from e
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            # Write an empty parquet file with no columns rather than skipping.
            table = pa.Table.from_pylist([])
        else:
            table = pa.Table.from_pylist(self._rows)
        pq.write_table(table, self.path)
