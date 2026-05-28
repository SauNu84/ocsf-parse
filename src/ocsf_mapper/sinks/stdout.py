"""Write JSONL to stdout. Useful for shell piping (``ocsf-mapper apply ... | jq .``)."""

from __future__ import annotations

import json
import sys

from ocsf_mapper.sinks.base import _SinkBase


class StdoutSink(_SinkBase):
    def write_one(self, event: dict) -> None:
        sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")

    def close(self) -> None:
        sys.stdout.flush()
