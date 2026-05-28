"""Tests for the schema-bump diff.

Uses a temp git repo as a fake schema to avoid coupling to whatever the
real ocsf-schema submodule looks like at any moment in time.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ocsf_mapper.schema import Schema
from ocsf_mapper.schema_diff import (
    affected_mappings,
    diff_against,
    load_class_at_ref,
    render_report,
)


def _git(*args: str, cwd: Path) -> None:
    """Run git in cwd with quiet output."""
    subprocess.run(
        ["git", *args], cwd=cwd, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


@pytest.fixture
def fake_schema(tmp_path: Path) -> Path:
    """Build a minimal git-backed schema with two commits.

    HEAD~1: ``foo`` class requires only attribute ``a``.
    HEAD:   ``foo`` class requires ``a`` and ``b``, plus a new ``bar`` class
            also exists.
    """
    root = tmp_path / "schema"
    (root / "events").mkdir(parents=True)
    # Required scaffolding: categories.json, dictionary.json, version.json.
    (root / "categories.json").write_text(json.dumps({
        "attributes": {"test": {"uid": 1, "caption": "Test"}}
    }))
    (root / "dictionary.json").write_text(json.dumps({"attributes": {}}))
    (root / "version.json").write_text(json.dumps({"version": "0.0.0-test"}))
    (root / "events" / "base_event.json").write_text(json.dumps({
        "name": "base_event", "caption": "Base Event", "attributes": {}
    }))
    # v1: foo with required `a`
    foo_v1 = {
        "name": "foo", "caption": "Foo", "uid": 1, "category": "test",
        "attributes": {
            "a": {"requirement": "required"},
            "b": {"requirement": "recommended"},
            "activity_id": {"enum": {"1": {"caption": "Launch"}}},
        },
    }
    (root / "events" / "foo.json").write_text(json.dumps(foo_v1))

    _git("init", "-b", "main", cwd=root)
    _git("config", "user.email", "t@example.com", cwd=root)
    _git("config", "user.name", "test", cwd=root)
    _git("add", ".", cwd=root)
    _git("commit", "-m", "v1", cwd=root)

    # v2: foo now requires both `a` and `b`, gains an at_least_one constraint
    # and an extra activity enum value; bar is a brand-new class.
    foo_v2 = {
        "name": "foo", "caption": "Foo", "uid": 1, "category": "test",
        "attributes": {
            "a": {"requirement": "required"},
            "b": {"requirement": "required"},  # was recommended
            "activity_id": {"enum": {"1": {"caption": "Launch"},
                                       "2": {"caption": "Terminate"}}},
        },
        "constraints": {"at_least_one": ["c", "d"]},
    }
    (root / "events" / "foo.json").write_text(json.dumps(foo_v2))

    bar = {
        "name": "bar", "caption": "Bar", "uid": 2, "category": "test",
        "attributes": {"x": {"requirement": "required"}},
    }
    (root / "events" / "bar.json").write_text(json.dumps(bar))

    _git("add", ".", cwd=root)
    _git("commit", "-m", "v2", cwd=root)

    return root


# ---------------------------------------------------------------------------
# load_class_at_ref
# ---------------------------------------------------------------------------


def test_load_class_at_ref_finds_old_version(fake_schema):
    old = load_class_at_ref("foo", fake_schema, "HEAD~1")
    assert old is not None
    assert old["attributes"]["b"]["requirement"] == "recommended"


def test_load_class_at_ref_returns_none_when_file_didnt_exist(fake_schema):
    # bar was added in v2; doesn't exist at HEAD~1.
    assert load_class_at_ref("bar", fake_schema, "HEAD~1") is None


# ---------------------------------------------------------------------------
# diff_against
# ---------------------------------------------------------------------------


def test_diff_against_detects_added_required(fake_schema):
    diff = diff_against("HEAD~1", schema=Schema(root=fake_schema))
    assert "foo" in diff
    assert diff["foo"]["added_required"] == ["b"]
    assert diff["foo"]["removed_required"] == []
    assert diff["foo"]["new_class"] is False


def test_diff_against_detects_new_class(fake_schema):
    diff = diff_against("HEAD~1", schema=Schema(root=fake_schema))
    assert "bar" in diff
    assert diff["bar"]["new_class"] is True
    assert diff["bar"]["added_required"] == ["x"]


def test_diff_against_detects_added_at_least_one(fake_schema):
    diff = diff_against("HEAD~1", schema=Schema(root=fake_schema))
    assert ("c", "d") in diff["foo"]["added_constraints"]


def test_diff_against_detects_new_activity_enum(fake_schema):
    diff = diff_against("HEAD~1", schema=Schema(root=fake_schema))
    assert "2" in diff["foo"]["added_activity"]


def test_diff_against_empty_when_no_changes(fake_schema):
    diff = diff_against("HEAD", schema=Schema(root=fake_schema))
    assert diff == {}


# ---------------------------------------------------------------------------
# affected_mappings + render_report
# ---------------------------------------------------------------------------


def test_affected_mappings_flags_missing_required(fake_schema, tmp_path):
    mappings = tmp_path / "mappings"
    mappings.mkdir()
    # Mapping declares the changed class but doesn't populate `b`.
    (mappings / "missing_b.json").write_text(json.dumps({
        "parser": "json",
        "classes": {"foo": {"mapping": {"a": {"const": 1}}}},
    }))
    # Mapping that does populate `b`.
    (mappings / "covers_b.json").write_text(json.dumps({
        "parser": "json",
        "classes": {"foo": {"mapping": {"a": {"const": 1}, "b": {"const": 2}}}},
    }))
    diff = diff_against("HEAD~1", schema=Schema(root=fake_schema))
    joined = affected_mappings(diff, mappings_dir=mappings)
    foo_info = joined["foo"]
    by_name = {m["name"]: m for m in foo_info["mappings"]}
    assert by_name["missing_b"]["missing"] == ["b"]
    assert by_name["covers_b"]["missing"] == []
    assert by_name["covers_b"]["populated"] == ["b"]


def test_render_report_includes_breakers(fake_schema, tmp_path):
    mappings = tmp_path / "mappings"
    mappings.mkdir()
    (mappings / "missing_b.json").write_text(json.dumps({
        "parser": "json",
        "classes": {"foo": {"mapping": {"a": {"const": 1}}}},
    }))
    diff = diff_against("HEAD~1", schema=Schema(root=fake_schema))
    joined = affected_mappings(diff, mappings_dir=mappings)
    text = render_report(joined)
    assert "class: foo" in text
    assert "+ required: ['b']" in text
    assert "missing_b" in text


def test_render_report_empty_when_no_changes():
    assert "No schema-affecting changes" in render_report({})


def test_render_report_handles_removed_attrs_and_no_mappings():
    """Cover the removed_required / removed_constraint / removed_activity /
    'no mapping declares this class' branches."""
    info = {
        "synthetic_class": {
            "new_class": False,
            "added_required": ["x"],
            "removed_required": ["y"],
            "added_constraints": [],
            "removed_constraints": [("a", "b")],
            "added_activity": [],
            "removed_activity": ["99"],
            "mappings": [],  # no mapping declares this class
        }
    }
    out = render_report(info)
    assert "no mapping declares this class" in out
    assert "- required: ['y']" in out
    assert "- at_least_one:" in out
    assert "- activity_id enum: ['99']" in out


def test_main_exits_nonzero_when_breakers_present(fake_schema, tmp_path, monkeypatch, capsys):
    mappings = tmp_path / "mappings"
    mappings.mkdir()
    (mappings / "missing_b.json").write_text(json.dumps({
        "parser": "json",
        "classes": {"foo": {"mapping": {"a": {"const": 1}}}},
    }))
    # Point the Schema loader at our fake schema, and pass `mappings` explicitly.
    monkeypatch.setenv("OCSF_SCHEMA_ROOT", str(fake_schema))
    from ocsf_mapper.schema_diff import main as schema_diff_main
    rc = schema_diff_main(["HEAD~1", str(mappings)])
    out = capsys.readouterr().out
    assert "missing_b" in out
    assert rc == 1


def test_main_exits_zero_when_no_breakers(fake_schema, tmp_path, monkeypatch):
    monkeypatch.setenv("OCSF_SCHEMA_ROOT", str(fake_schema))
    from ocsf_mapper.schema_diff import main as schema_diff_main
    # No mappings dir → no breakers possible
    rc = schema_diff_main(["HEAD~1", str(tmp_path / "empty")])
    assert rc == 0


def test_main_handles_missing_schema(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OCSF_SCHEMA_ROOT", str(tmp_path / "does-not-exist"))
    from ocsf_mapper.schema_diff import main as schema_diff_main
    rc = schema_diff_main(["HEAD~1", "mappings"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error" in err.lower()
