"""Tests for catalog.py."""

from __future__ import annotations

import json

from ocsf_mapper.catalog import join_catalog, load_catalog, main


def test_load_catalog_returns_entries(repo_root):
    cat = load_catalog(repo_root / "catalog.json")
    assert cat["ocsf_schema_version"] == "1.9.0-dev"
    assert len(cat["entries"]) >= 17
    src_names = {e["source"] for e in cat["entries"]}
    assert {"cloudtrail", "windows_event_log", "crowdstrike_falcon"} <= src_names


def test_join_catalog_marks_all_mapped(repo_root, monkeypatch):
    monkeypatch.chdir(repo_root)
    rows = join_catalog()
    assert rows, "expected catalog rows"
    statuses = {r["status"] for r in rows}
    # All entries should be 'mapped' (samples + mappings shipped together).
    assert statuses == {"mapped"}, f"unexpected statuses: {statuses}"


def test_join_catalog_marks_missing_when_no_mapping_file(tmp_path):
    cat = {
        "ocsf_schema_version": "x",
        "entries": [
            {
                "source": "nonexistent_source",
                "display_name": "Phantom",
                "vendor": "Unknown",
                "ocsf": {"category_uid": 1, "category_name": "x", "class_uid": 1001, "class_name": "y"},
                "priority": "low",
                "description": "",
            }
        ],
    }
    (tmp_path / "catalog.json").write_text(json.dumps(cat))
    (tmp_path / "mappings").mkdir()
    rows = join_catalog(tmp_path / "catalog.json", tmp_path / "mappings")
    assert rows[0]["status"] == "missing"
    assert rows[0]["sample_path"] is None


def test_main_prints_table_and_exits_zero(repo_root, monkeypatch, capsys):
    monkeypatch.chdir(repo_root)
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "LOG SOURCE" in out
    assert "VENDOR" in out
    assert "Total:" in out


def test_main_missing_catalog_returns_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main([str(tmp_path / "nope.json")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error" in err.lower()
