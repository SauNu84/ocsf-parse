"""Tests for the LLM provider abstraction."""

from __future__ import annotations

import json

import pytest

from ocsf_mapper.providers import (
    FixtureProvider,
    LLMProvider,
    get_provider,
)


# ---------------------------------------------------------------------------
# FixtureProvider
# ---------------------------------------------------------------------------


def test_fixture_provider_returns_responses_in_order(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps(["one", "two"]))
    p = FixtureProvider(fixture_dir=tmp_path, source="x")
    assert p.complete("anything") == "one"
    assert p.complete("anything") == "two"


def test_fixture_provider_clamps_to_last_response(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps(["only"]))
    p = FixtureProvider(fixture_dir=tmp_path, source="x")
    assert p.complete("a") == "only"
    assert p.complete("a") == "only"


def test_fixture_provider_accepts_string_fixture(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps("just a string"))
    p = FixtureProvider(fixture_dir=tmp_path, source="x")
    assert p.complete("a") == "just a string"


def test_fixture_provider_raises_on_missing_fixture(tmp_path):
    p = FixtureProvider(fixture_dir=tmp_path, source="nope")
    with pytest.raises(RuntimeError) as exc:
        p.complete("hi")
    assert "no fixtures loaded" in str(exc.value).lower()


def test_fixture_provider_rejects_non_list_non_string(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps({"a": 1}))
    with pytest.raises(ValueError):
        FixtureProvider(fixture_dir=tmp_path, source="x")


def test_fixture_provider_satisfies_protocol(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps(["r"]))
    p = FixtureProvider(fixture_dir=tmp_path, source="x")
    assert isinstance(p, LLMProvider)


# ---------------------------------------------------------------------------
# get_provider — env detection
# ---------------------------------------------------------------------------


def test_get_provider_explicit_fixture(monkeypatch, tmp_path):
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv("OCSF_LLM_FIXTURE_DIR", str(tmp_path))
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "x")
    (tmp_path / "x.json").write_text(json.dumps(["hello"]))
    p = get_provider()
    assert p.name == "fixture"
    assert p.complete("hi") == "hello"


def test_get_provider_unknown_name_raises(monkeypatch):
    monkeypatch.delenv("OCSF_LLM_PROVIDER", raising=False)
    with pytest.raises(ValueError):
        get_provider(name="not-a-real-provider")


def test_get_provider_no_keys_set_raises(monkeypatch):
    monkeypatch.delenv("OCSF_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        get_provider()
