"""Tests for the mapping registry."""

from __future__ import annotations

import json
from pathlib import Path

from ocsf_mapper.registry import list_mappings


def test_list_mappings_empty_for_missing_dir(tmp_path):
    assert list_mappings(tmp_path / "nope") == []


def test_list_mappings_summarizes_repo_mappings(mappings_dir):
    out = list_mappings(mappings_dir)
    names = {m["name"] for m in out}
    assert {"cloudtrail", "okta", "palo_alto"} <= names
    ct = next(m for m in out if m["name"] == "cloudtrail")
    assert ct["parser_kind"] == "json"
    assert set(ct["classes"]) == {"authentication", "api_activity"}
    assert ct["sample"] and ct["sample"].endswith("cloudtrail.jsonl")


def test_list_mappings_surfaces_metadata_fields(mappings_dir):
    out = list_mappings(mappings_dir)
    ct = next(m for m in out if m["name"] == "cloudtrail")
    assert ct["display_name"] == "AWS CloudTrail"
    assert ct["vendor"] == "AWS"
    assert ct["priority"] == "critical"
    assert "AWS management-plane" in ct["description"]


def test_list_mappings_surfaces_mapping_version(mappings_dir):
    """Every reference mapping carries a mapping_version (Bucket C #3)."""
    out = list_mappings(mappings_dir)
    for m in out:
        assert m["mapping_version"] == "1.0.0", (
            f"{m['name']!r}: version was {m['mapping_version']!r}, expected 1.0.0"
        )


def test_list_mappings_mapping_version_defaults_when_absent(tmp_path):
    import json
    mapdir = tmp_path / "mappings"
    mapdir.mkdir()
    (mapdir / "no_version.json").write_text(json.dumps({"parser": "json", "classes": {}}))
    out = list_mappings(mapdir)
    assert out[0]["mapping_version"] == "0.0.0"


def test_list_mappings_parser_kind_cef_and_leef(tmp_path):
    import json
    mapdir = tmp_path / "mappings"
    mapdir.mkdir()
    (mapdir / "a.json").write_text(json.dumps({"parser": "cef",  "classes": {}}))
    (mapdir / "b.json").write_text(json.dumps({"parser": "leef", "classes": {}}))
    out = {m["name"]: m for m in list_mappings(mapdir)}
    assert out["a"]["parser_kind"] == "cef"
    assert out["b"]["parser_kind"] == "leef"


def test_list_mappings_falls_back_when_metadata_missing(tmp_path):
    import json
    mapdir = tmp_path / "mappings"
    mapdir.mkdir()
    (mapdir / "bare.json").write_text(json.dumps({"parser": "json", "classes": {}}))
    out = list_mappings(mapdir)
    assert out[0]["display_name"] == "bare"
    assert out[0]["vendor"] == "Unknown"
    assert out[0]["priority"] == "medium"
    assert out[0]["description"] == ""


def test_list_mappings_skips_underscore_prefixed(tmp_path):
    (tmp_path / "_scratch.json").write_text("{}")
    (tmp_path / "real.json").write_text(json.dumps({"classes": {"c": {"mapping": {}}}}))
    out = list_mappings(tmp_path)
    assert [m["name"] for m in out] == ["real"]


def test_list_mappings_skips_malformed_json(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "ok.json").write_text(json.dumps({"classes": {"c": {"mapping": {}}}}))
    out = list_mappings(tmp_path)
    assert [m["name"] for m in out] == ["ok"]


def test_list_mappings_sample_fallback_underscore_sample(tmp_path):
    (tmp_path / "mappings").mkdir()
    (tmp_path / "samples").mkdir()
    (tmp_path / "mappings" / "foo.json").write_text(json.dumps({"classes": {}, "parser": "json"}))
    sample = tmp_path / "samples" / "foo_sample.jsonl"
    sample.write_text("")
    out = list_mappings(tmp_path / "mappings")
    assert out[0]["sample"] == str(sample)


def test_list_mappings_regex_parser_kind(tmp_path):
    (tmp_path / "mappings").mkdir()
    (tmp_path / "mappings" / "x.json").write_text(
        json.dumps({"parser": {"regex": "^.*$", "groups": []}, "classes": {}})
    )
    out = list_mappings(tmp_path / "mappings")
    assert out[0]["parser_kind"] == "regex"
