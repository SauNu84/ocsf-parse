"""Security Lake-style partitioned Parquet sink.

Writes Parquet files to a directory layout compatible with AWS Security
Lake's custom-source ingest::

    <root>/<class_uid>/eventDay=YYYYMMDD/part-NNNNN.parquet

``class_uid`` is a bare directory (no Hive ``key=`` prefix), matching
Security Lake convention; ``eventDay`` uses Hive style.

Memory model
------------

Events are bucketed by ``(class_uid, eventDay)``. Each bucket flushes to
a fresh ``part-NNNNN.parquet`` file as soon as it reaches
``flush_every`` rows (default 50 000) — so memory is bounded regardless
of total input size. Any remaining tail rows flush on :meth:`close`.

Existing part files in a partition aren't overwritten — the next part
number is computed by counting files already on disk at the time the
bucket is first touched. That means re-running the sink on the same
``<root>`` appends new parts rather than clobbering.

Optional fast-path
------------------

Pass ``schema=`` (a ``pyarrow.Schema``) to pre-declare the column types.
Without it pyarrow re-infers per flush, which is correct but slow at scale.
A helper :func:`infer_schema_from` builds one from a sample event.

Requires the optional ``pyarrow`` dependency:

    pip install ocsf-mapper[parquet]
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ocsf_mapper.sinks.base import _SinkBase


_DEFAULT_FLUSH_EVERY = 50_000


class SecurityLakeSink(_SinkBase):
    """Partitioned, streaming Parquet writer for Security Lake-compatible output."""

    def __init__(
        self,
        root: Path | str,
        file_prefix: str = "part",
        flush_every: int = _DEFAULT_FLUSH_EVERY,
        schema: Optional[Any] = None,
    ) -> None:
        try:
            import pyarrow  # noqa: F401
            import pyarrow.parquet  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "SecurityLakeSink requires pyarrow. "
                "Install with: pip install ocsf-mapper[parquet]"
            ) from e
        if flush_every <= 0:
            raise ValueError("flush_every must be positive")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.file_prefix = file_prefix
        self.flush_every = flush_every
        self.schema = schema
        self._buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
        # Track the next part-N to write per partition. Initialised lazily on
        # first touch by counting pre-existing files (so we never overwrite).
        self._next_part: dict[tuple[str, str], int] = {}
        self._flushed_rows: dict[tuple[str, str], int] = defaultdict(int)

    # -- Sink protocol ---------------------------------------------------

    def write_one(self, event: dict) -> None:
        key = self._partition_key(event)
        bucket = self._buckets[key]
        bucket.append(event)
        if len(bucket) >= self.flush_every:
            self._flush_partition(key)

    def write_many(self, events: Iterable[dict]) -> int:
        n = 0
        for ev in events:
            self.write_one(ev)
            n += 1
        return n

    def close(self) -> None:
        # Flush any tail rows that never hit the threshold.
        for key in list(self._buckets):
            if self._buckets[key]:
                self._flush_partition(key)

    # -- introspection ---------------------------------------------------

    def partitions(self) -> dict[tuple[str, str], int]:
        """Buffered rows per partition (not yet flushed)."""
        return {k: len(v) for k, v in self._buckets.items() if v}

    def flushed_rows(self) -> dict[tuple[str, str], int]:
        """Rows already on disk per partition (this session)."""
        return dict(self._flushed_rows)

    # -- helpers ---------------------------------------------------------

    def _flush_partition(self, key: tuple[str, str]) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows = self._buckets[key]
        if not rows:
            return
        cls_uid, event_day = key
        dirpath = self.root / cls_uid / f"eventDay={event_day}"
        dirpath.mkdir(parents=True, exist_ok=True)

        # Lazily pick the next part-N for this partition by counting what's
        # already on disk (covers both same-session flushes and prior runs).
        if key not in self._next_part:
            self._next_part[key] = sum(
                1 for _ in dirpath.glob(f"{self.file_prefix}-*.parquet")
            )
        part_n = self._next_part[key]
        self._next_part[key] = part_n + 1

        target = dirpath / f"{self.file_prefix}-{part_n:05d}.parquet"
        table = pa.Table.from_pylist(rows, schema=self.schema)
        pq.write_table(table, target)
        self._flushed_rows[key] += len(rows)
        self._buckets[key] = []

    def _partition_key(self, event: dict) -> tuple[str, str]:
        cls_uid = event.get("class_uid")
        event_day = "unknown"
        t = event.get("time")
        if isinstance(t, int) and t > 0:
            event_day = datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y%m%d")
        return str(cls_uid if cls_uid is not None else "unknown"), event_day


def infer_schema_from(event: dict):
    """Build a :class:`pyarrow.Schema` from one sample event.

    Helpful when you've got a 10 TB input and want to skip pyarrow's
    per-flush type inference. Pass the result to ``SecurityLakeSink(...,
    schema=...)`` or :class:`ParquetSink(..., schema=...)`.
    """
    import pyarrow as pa
    return pa.Table.from_pylist([event]).schema
