"""Write JSONL to stdout. Useful for shell piping (``ocsf-mapper apply ... | jq .``)."""

from __future__ import annotations

import sys

from ocsf_mapper._fastjson import dumps as _json_dumps
from ocsf_mapper.sinks.base import _SinkBase


class StdoutSink(_SinkBase):
    def write_one(self, event: dict) -> None:
        sys.stdout.write(_json_dumps(event) + "\n")

    def close(self) -> None:
        sys.stdout.flush()
