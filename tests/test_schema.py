"""Tests for the schema loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from ocsf_mapper.schema import (
    Schema, default_schema_root, list_available_versions, resolve_schema_root,
)


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


def test_list_available_versions_default_first():
    """The current submodule is always first; pinned alternates follow."""
    versions = list_available_versions()
    assert versions, "expected at least the default schema to be listed"
    assert versions[0]["is_default"] is True
    assert all(not v["is_default"] for v in versions[1:])


def test_pinned_v1_8_0_loads():
    """The v1.8.0 worktree (materialised by setup_schema_versions.sh) loads
    cleanly and reports its own version string."""
    root = resolve_schema_root("1.8.0")
    s = Schema(root=root)
    assert s.version() == "1.8.0"
    # Sanity: it still has the core authentication class.
    auth = s.load_class("authentication")
    assert "auth_protocol" in auth["attributes"]


def test_pinned_version_missing_raises(tmp_path, monkeypatch):
    """Asking for a version that isn't materialised on disk surfaces a
    clear error pointing at the setup script."""
    with pytest.raises(FileNotFoundError, match="setup_schema_versions.sh"):
        resolve_schema_root("99.99.99")
