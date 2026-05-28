"""Newline-delimited JSON sink — one OCSF event per line."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

from ocsf_mapper.sinks.base import _SinkBase


class JsonlSink(_SinkBase):
    """Write OCSF events as JSONL (one JSON object per line).

    The default for re-ingestion into NDJSON-aware tools (jq, vector, fluentbit).
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp: TextIO = self.path.open("w", encoding="utf-8")

    def write_one(self, event: dict) -> None:
        self._fp.write(json.dumps(event, ensure_ascii=False))
        self._fp.write("\n")

    def close(self) -> None:
        if not self._fp.closed:
            self._fp.close()
