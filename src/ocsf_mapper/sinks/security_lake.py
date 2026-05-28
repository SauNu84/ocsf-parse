"""Security Lake-style partitioned Parquet sink.

Writes one Parquet file per ``class_uid × eventDay`` bucket to a directory
layout compatible with AWS Security Lake's custom-source ingest::

    <root>/<class_uid>/eventDay=YYYYMMDD/part-<n>.parquet

``class_uid`` is a bare directory name (no Hive ``key=`` prefix), matching
Security Lake's convention; ``eventDay`` uses Hive-style. ``region`` and
``account_id`` keys can be added by future revisions when we have a
concrete need.

Requires the optional ``pyarrow`` dependency:

    pip install ocsf-mapper[parquet]
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from ocsf_mapper.sinks.base import _SinkBase


class SecurityLakeSink(_SinkBase):
    """Partitioned Parquet writer for Security Lake-compatible output.

    Events are buffered in memory, grouped by ``(class_uid, eventDay)``, and
    flushed to one Parquet file per partition on :meth:`close`. Each event's
    ``time`` field (epoch ms) determines its ``eventDay``; events missing
    ``time`` are bucketed under ``eventDay=unknown``.
    """

    def __init__(self, root: Path | str, file_prefix: str = "part") -> None:
        try:
            import pyarrow  # noqa: F401
            import pyarrow.parquet  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "SecurityLakeSink requires pyarrow. "
                "Install with: pip install ocsf-mapper[parquet]"
            ) from e
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.file_prefix = file_prefix
        self._buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)

    # -- Sink protocol ---------------------------------------------------

    def write_one(self, event: dict) -> None:
        key = self._partition_key(event)
        self._buckets[key].append(event)

    def write_many(self, events: Iterable[dict]) -> int:
        n = 0
        for ev in events:
            self.write_one(ev)
            n += 1
        return n

    def close(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        for (cls_uid, event_day), events in self._buckets.items():
            d = self.root / str(cls_uid) / f"eventDay={event_day}"
            d.mkdir(parents=True, exist_ok=True)
            target = d / f"{self.file_prefix}-{len(list(d.glob('*.parquet'))):05d}.parquet"
            table = pa.Table.from_pylist(events)
            pq.write_table(table, target)

    # -- helpers ---------------------------------------------------------

    def _partition_key(self, event: dict) -> tuple[str, str]:
        cls_uid = event.get("class_uid")
        event_day = "unknown"
        t = event.get("time")
        if isinstance(t, int) and t > 0:
            event_day = datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y%m%d")
        return str(cls_uid if cls_uid is not None else "unknown"), event_day

    def partitions(self) -> dict[tuple[str, str], int]:
        """Return current bucket counts ``{(class_uid, eventDay): n_events}``."""
        return {k: len(v) for k, v in self._buckets.items()}
