"""Drop-in JSON helpers — orjson if installed, stdlib json otherwise.

Why this exists: on JSON-shaped sources (CloudTrail, Okta, Cloudflare,
WAF, etc.) the per-event JSON parse is one of the largest line items in
``apply_stream``'s hot path. ``orjson`` is 5-10× faster than stdlib for
both ``loads`` and ``dumps``. Adding it as an optional dependency means
the speedup is opt-in (``pip install ocsf-mapper[fast]``) and the package
keeps a clean zero-dependency floor.

Module-level constants:

  HAS_ORJSON: bool   True iff orjson imported successfully

Functions:

  loads(s)  — accepts str or bytes; returns the parsed object
  dumps(o)  — returns a *str* (so callers don't need to know the backend)
"""

from __future__ import annotations

import json as _stdlib_json
from typing import Any, Union

try:
    import orjson as _orjson  # type: ignore[import-not-found]
    HAS_ORJSON = True
except ImportError:  # pragma: no cover - exercised only on installs without orjson
    _orjson = None
    HAS_ORJSON = False


if HAS_ORJSON:
    def loads(s: Union[str, bytes]) -> Any:
        # orjson is fastest on bytes; encode str inputs once.
        if isinstance(s, str):
            return _orjson.loads(s.encode("utf-8"))
        return _orjson.loads(s)

    def dumps(obj: Any) -> str:
        # orjson.dumps returns bytes; decode once at the boundary so callers
        # treat the result like the stdlib's str output.
        return _orjson.dumps(obj).decode("utf-8")
else:
    def loads(s: Union[str, bytes]) -> Any:
        if isinstance(s, bytes):
            s = s.decode("utf-8")
        return _stdlib_json.loads(s)

    def dumps(obj: Any) -> str:
        # Match orjson's behaviour: no spaces, UTF-8 escapes off.
        return _stdlib_json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
