"""Tests for the lint runner."""

from __future__ import annotations

import json
from pathlib import Path

from ocsf_mapper.lint import lint, lint_one, main
from ocsf_mapper.schema import Schema


def test_lint_repo_mappings_all_pass(mappings_dir, monkeypatch):
    monkeypatch.chdir(mappings_dir.parent)
    results = lint(mappings_dir)
    assert results, "expected at least one mapping to lint"
    for r in results:
        assert r["status"] == "OK", r


def test_lint_one_skips_when_no_sample(tmp_path, schema):
    mapping = tmp_path / "foo.json"
    mapping.write_text(json.dumps({"parser": "json", "classes": {}}))
    r = lint_one(mapping, sample_path=None, schema=schema)
    assert r["status"] == "SKIP"


def test_lint_one_warns_when_mapping_version_missing(tmp_path, schema):
    """Bucket C #3 — mappings without mapping_version warn but don't fail."""
    mapping = tmp_path / "no_version.json"
    mapping.write_text(json.dumps({
        "parser": "json",
        "classes": {"authentication": {"mapping": {"k": {"const": 1}}}},
    }))
    sample = tmp_path / "no_version.jsonl"
    sample.write_text("{}\n")
    r = lint_one(mapping, sample_path=sample, schema=schema)
    # Validation will fail (missing required attrs), but the version warning
    # should fire regardless of the lint outcome.
    assert any("mapping_version missing" in w for w in r.get("warnings", []))


def test_lint_one_no_warning_when_mapping_version_present(tmp_path, schema):
    import json as _j
    mapping = tmp_path / "with_version.json"
    mapping.write_text(_j.dumps({
        "mapping_version": "1.0.0",
        "parser": "json",
        "classes": {"authentication": {"mapping": {"k": {"const": 1}}}},
    }))
    sample = tmp_path / "with_version.jsonl"
    sample.write_text("{}\n")
    r = lint_one(mapping, sample_path=sample, schema=schema)
    assert not any("mapping_version missing" in w for w in r.get("warnings", []))


def test_lint_one_flags_malformed_json(tmp_path, schema):
    mapping = tmp_path / "bad.json"
    mapping.write_text("{not json")
    sample = tmp_path / "bad.jsonl"
    sample.write_text("{}\n")
    r = lint_one(mapping, sample_path=sample, schema=schema)
    assert r["status"] == "FAIL"
    assert any("not valid JSON" in e for e in r["errors"])


def test_lint_one_flags_apply_crash(tmp_path, schema):
    # Unknown parser kind makes apply_stream_with_class raise inside lint_one.
    mapping = tmp_path / "x.json"
    mapping.write_text(
        json.dumps({"parser": "no-such-parser", "classes": {"c": {"mapping": {}}}})
    )
    sample = tmp_path / "x.jsonl"
    sample.write_text("anything\n")
    r = lint_one(mapping, sample_path=sample, schema=schema)
    assert r["status"] == "FAIL"
    assert any("apply crashed" in e for e in r["errors"])


def test_lint_one_flags_validation_failure(tmp_path, schema):
    # Map to authentication but omit every required attribute → validator fails.
    mapping = tmp_path / "bare.json"
    mapping.write_text(
        json.dumps(
            {
                "parser": "json",
                "classes": {
                    "authentication": {
                        "mapping": {
                            "some_field": {"const": "x"},
                        }
                    }
                },
            }
        )
    )
    sample = tmp_path / "bare.jsonl"
    sample.write_text("{}\n")
    r = lint_one(mapping, sample_path=sample, schema=schema)
    assert r["status"] == "FAIL"
    assert any("missing required" in e for e in r["errors"])


def test_lint_one_coalesces_repeated_event_errors(tmp_path, schema):
    """When ≥3 sample events fail with identical errors, lint_one
    collapses them into one summary line instead of N near-duplicates.

    Without this, a 100-event sample bloats the audit log entry and the
    web UI error list with 100 copies of the same message.
    """
    mapping = tmp_path / "bare.json"
    mapping.write_text(json.dumps({
        "mapping_version": "1.0.0",
        "parser": "json",
        "classes": {
            "authentication": {"mapping": {"some_field": {"const": "x"}}}
        },
    }))
    sample = tmp_path / "bare.jsonl"
    sample.write_text("{}\n" * 10)
    r = lint_one(mapping, sample_path=sample, schema=schema)
    assert r["status"] == "FAIL"
    err_lines = [e for e in r["errors"] if "missing required" in e]
    assert len(err_lines) == 1, err_lines
    assert "events #1-#10" in err_lines[0]
    assert "(authentication)" in err_lines[0]


def test_lint_one_does_not_coalesce_below_threshold(tmp_path, schema):
    """With <3 failing events, each gets its own line — preserves
    per-event detail when there's little to dedupe."""
    mapping = tmp_path / "bare.json"
    mapping.write_text(json.dumps({
        "mapping_version": "1.0.0",
        "parser": "json",
        "classes": {
            "authentication": {"mapping": {"some_field": {"const": "x"}}}
        },
    }))
    sample = tmp_path / "bare.jsonl"
    sample.write_text("{}\n{}\n")
    r = lint_one(mapping, sample_path=sample, schema=schema)
    assert r["status"] == "FAIL"
    err_lines = [e for e in r["errors"] if "missing required" in e]
    assert len(err_lines) == 2
    assert err_lines[0].startswith("event #1 (authentication):")
    assert err_lines[1].startswith("event #2 (authentication):")


def test_main_with_empty_folder_exits_zero(tmp_path, capsys):
    rc = main(str(tmp_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "no mappings found" in out


def test_main_with_repo_mappings_exits_zero(mappings_dir, monkeypatch, capsys):
    monkeypatch.chdir(mappings_dir.parent)
    rc = main(str(mappings_dir))
    assert rc == 0
    out = capsys.readouterr().out
    assert "OVERALL: PASS" in out
