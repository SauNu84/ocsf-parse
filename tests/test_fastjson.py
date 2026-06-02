"""Tests for the orjson/stdlib JSON shim."""

from __future__ import annotations

import json as _stdlib

import pytest

from ocsf_mapper import _fastjson


# ---------------------------------------------------------------------------
# Active backend (whichever is installed)
# ---------------------------------------------------------------------------


def test_loads_accepts_str_and_bytes():
    assert _fastjson.loads('{"a": 1}') == {"a": 1}
    assert _fastjson.loads(b'{"a": 1}') == {"a": 1}


def test_dumps_returns_str_compact():
    out = _fastjson.dumps({"a": 1, "b": "x"})
    assert isinstance(out, str)
    # Both backends are configured to emit compact output (no spaces).
    assert " " not in out
    assert _stdlib.loads(out) == {"a": 1, "b": "x"}


def test_roundtrip_with_nested_structures():
    ev = {
        "metadata": {"product": {"name": "nginx"}},
        "tags": ["a", "b", 1, 2],
        "stats": {"bytes": 1234, "ok": True, "extra": None},
    }
    out = _fastjson.dumps(ev)
    assert _fastjson.loads(out) == ev


# ---------------------------------------------------------------------------
# Force the stdlib branch even when orjson is installed
# ---------------------------------------------------------------------------


@pytest.fixture
def stdlib_only(monkeypatch):
    """Reload _fastjson with orjson hidden so we exercise the stdlib branch.

    Uses the documented ``sys.modules[name] = None`` trick to mask a module —
    ``import name`` then raises ``ModuleNotFoundError``. This is more
    portable than registering a meta_path finder, which on Python 3.12+
    no longer calls the legacy ``find_module`` / ``load_module`` API.
    """
    import importlib
    import sys

    # Stash whatever's there.
    saved_orjson = sys.modules.get("orjson")

    # Setting sys.modules[name] = None is the canonical way to make
    # `import name` raise ModuleNotFoundError, regardless of whether the
    # module is actually installed on disk.
    monkeypatch.setitem(sys.modules, "orjson", None)

    # Force reload of _fastjson so its module-level `try: import orjson`
    # runs against the masked entry.
    monkeypatch.delitem(sys.modules, "ocsf_mapper._fastjson", raising=False)
    fj = importlib.import_module("ocsf_mapper._fastjson")
    assert fj.HAS_ORJSON is False

    try:
        yield fj
    finally:
        # Restore orjson (monkeypatch already handles unwind, but be
        # explicit so the next test sees the real module) and reload
        # _fastjson so other tests get the orjson-backed version.
        if saved_orjson is not None:
            sys.modules["orjson"] = saved_orjson
        else:
            sys.modules.pop("orjson", None)
        sys.modules.pop("ocsf_mapper._fastjson", None)
        importlib.import_module("ocsf_mapper._fastjson")


def test_stdlib_branch_roundtrip(stdlib_only):
    ev = {"metadata": {"product": {"name": "nginx"}}, "x": 1}
    out = stdlib_only.dumps(ev)
    assert isinstance(out, str)
    assert " " not in out  # stdlib branch uses separators=(",", ":")
    assert stdlib_only.loads(out) == ev


def test_stdlib_branch_loads_accepts_bytes(stdlib_only):
    assert stdlib_only.loads(b'{"a": 1}') == {"a": 1}
