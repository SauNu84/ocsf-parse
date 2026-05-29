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
    """Reload _fastjson with orjson hidden so we exercise the stdlib branch."""
    import importlib
    import sys

    # Stash whatever's there.
    saved_orjson = sys.modules.pop("orjson", None)

    # Block import of orjson during reimport.
    class _Block:
        def find_module(self, name, path=None):
            return self if name == "orjson" else None

        def load_module(self, name):
            raise ImportError("orjson blocked for test")

    sys.meta_path.insert(0, _Block())
    try:
        sys.modules.pop("ocsf_mapper._fastjson", None)
        fj = importlib.import_module("ocsf_mapper._fastjson")
        assert fj.HAS_ORJSON is False
        yield fj
    finally:
        sys.meta_path.pop(0)
        if saved_orjson is not None:
            sys.modules["orjson"] = saved_orjson
        sys.modules.pop("ocsf_mapper._fastjson", None)
        # Restore so other tests see the normal module.
        importlib.import_module("ocsf_mapper._fastjson")


def test_stdlib_branch_roundtrip(stdlib_only):
    ev = {"metadata": {"product": {"name": "nginx"}}, "x": 1}
    out = stdlib_only.dumps(ev)
    assert isinstance(out, str)
    assert " " not in out  # stdlib branch uses separators=(",", ":")
    assert stdlib_only.loads(out) == ev


def test_stdlib_branch_loads_accepts_bytes(stdlib_only):
    assert stdlib_only.loads(b'{"a": 1}') == {"a": 1}
