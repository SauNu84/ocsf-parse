"""CSV sink — flattens nested OCSF objects to dotted column names.

Trade-offs:
  * Lossy for nested arrays (e.g. ``actor.attacks`` becomes a JSON-stringified cell).
  * The column set is the union of all keys seen — writes a header on close.

For high-fidelity exports prefer :class:`ocsf_mapper.sinks.JsonlSink` or
:class:`ocsf_mapper.sinks.parquet.ParquetSink`.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from ocsf_mapper.sinks.base import _SinkBase


def _flatten(obj: dict, prefix: str = "") -> dict:
    """Flatten a nested dict to dotted keys. Lists become JSON strings."""
    out: dict = {}
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        elif isinstance(v, list):
            out[key] = json.dumps(v, ensure_ascii=False)
        else:
            out[key] = v
    return out


class CsvSink(_SinkBase):
    """Buffered CSV sink. Header is written on :meth:`close`."""

    def __init__(self, path: Path | str, flatten: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.flatten = flatten
        self._rows: list[dict] = []
        self._columns: list[str] = []
        self._seen: set[str] = set()

    def write_one(self, event: dict) -> None:
        row = _flatten(event) if self.flatten else event
        for k in row.keys():
            if k not in self._seen:
                self._seen.add(k)
                self._columns.append(k)
        self._rows.append(row)

    def write_many(self, events: Iterable[dict]) -> int:
        n = 0
        for ev in events:
            self.write_one(ev)
            n += 1
        return n

    def close(self) -> None:
        with self.path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=self._columns, extrasaction="ignore")
            writer.writeheader()
            for row in self._rows:
                writer.writerow(row)
