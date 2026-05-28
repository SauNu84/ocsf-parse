"""Fixture provider — reads canned LLM responses from disk.

Used for tests and offline development. Set ``OCSF_LLM_PROVIDER=fixture`` and
point ``OCSF_LLM_FIXTURE_DIR`` at a directory containing ``<source>.json`` files.

Each fixture file is a list of canned response strings, consumed in order::

    [
      "<phase 1 response text>",
      "<phase 2 response text>",
      ...
    ]

A single-string fixture is also accepted.

The fixture provider keeps a per-source response cursor in memory so the same
generator run will pick the phase-1 then phase-2 responses correctly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


class FixtureProvider:
    name = "fixture"
    default_model = "fixture"

    def __init__(self, fixture_dir: Optional[Path | str] = None, source: Optional[str] = None) -> None:
        self.model = self.default_model
        self._dir = Path(fixture_dir or os.environ.get("OCSF_LLM_FIXTURE_DIR", "tests/fixtures/llm"))
        self._source = source or os.environ.get("OCSF_LLM_FIXTURE_SOURCE", "default")
        self._cursor = 0
        self._responses: list[str] = self._load()

    def _load(self) -> list[str]:
        p = self._dir / f"{self._source}.json"
        if not p.exists():
            return []
        data = json.loads(p.read_text())
        if isinstance(data, str):
            return [data]
        if isinstance(data, list):
            return [str(x) if not isinstance(x, str) else x for x in data]
        raise ValueError(f"fixture {p} must be a list or string, got {type(data).__name__}")

    def complete(self, prompt: str, system: str = "", max_tokens: int = 8000) -> str:
        if not self._responses:
            raise RuntimeError(
                f"No fixtures loaded for source={self._source!r} in {self._dir}. "
                f"Add {self._dir}/{self._source}.json with canned responses."
            )
        idx = min(self._cursor, len(self._responses) - 1)
        self._cursor += 1
        return self._responses[idx]
