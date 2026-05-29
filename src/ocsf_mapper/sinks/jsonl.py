"""Newline-delimited JSON sink — one OCSF event per line."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from ocsf_mapper._fastjson import dumps as _json_dumps
from ocsf_mapper.sinks.base import _SinkBase


class JsonlSink(_SinkBase):
    """Write OCSF events as JSONL (one JSON object per line).

    The default for re-ingestion into NDJSON-aware tools (jq, vector, fluentbit).
    Uses :mod:`ocsf_mapper._fastjson` for serialisation — picks orjson when
    available (~5× faster on dumps), stdlib json otherwise.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp: TextIO = self.path.open("w", encoding="utf-8")

    def write_one(self, event: dict) -> None:
        self._fp.write(_json_dumps(event))
        self._fp.write("\n")

    def close(self) -> None:
        if not self._fp.closed:
            self._fp.close()
