"""Tests for the schema loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from ocsf_mapper.schema import Schema, default_schema_root


def test_default_root_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OCSF_SCHEMA_ROOT", str(tmp_path))
    assert default_schema_root() == tmp_path


def test_default_root_without_env_points_at_submodule(monkeypatch):
    monkeypatch.delenv("OCSF_SCHEMA_ROOT", raising=False)
    assert default_schema_root().name == "ocsf-schema"


def test_missing_root_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Schema(root=tmp_path / "does-not-exist")


def test_version_matches_file(schema):
    assert schema.version() == "1.9.0-dev"


def test_categories_has_8_entries(schema):
    cats = schema.categories()
    assert len(cats["attributes"]) == 8


def test_dictionary_loads(schema):
    d = schema.dictionary()
    assert "time" in d["attributes"]


def test_load_class_merges_extends_chain(schema):
    auth = schema.load_class("authentication")
    # Inherited from base_event:
    assert "time" in auth["attributes"]
    # Own:
    assert "auth_protocol" in auth["attributes"]


def test_class_summaries_excludes_base_event(schema):
    summaries = schema.class_summaries()
    assert all(s["name"] != "base_event" for s in summaries)
    assert any(s["name"] == "authentication" for s in summaries)


def test_load_class_unknown_raises(schema):
    with pytest.raises(FileNotFoundError):
        schema.load_class("not_a_real_class")
