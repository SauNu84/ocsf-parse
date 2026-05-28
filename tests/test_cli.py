"""Tests for the CLI entry point (`ocsf-mapper`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ocsf_mapper.cli import main


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def test_cli_apply_writes_jsonl_to_file(tmp_path, mappings_dir, samples_dir, monkeypatch):
    monkeypatch.chdir(mappings_dir.parent)
    out = tmp_path / "out.jsonl"
    rc = main(["apply", str(mappings_dir / "cloudtrail.json"),
               str(samples_dir / "cloudtrail.jsonl"), str(out)])
    assert rc == 0
    assert out.exists()
    lines = out.read_text().splitlines()
    assert len(lines) == 100
    first = json.loads(lines[0])
    assert first["class_uid"] in (3002, 6003)


def test_cli_apply_pipes_to_stdout_when_output_omitted(mappings_dir, samples_dir, capsys, monkeypatch):
    monkeypatch.chdir(mappings_dir.parent)
    rc = main(["apply", str(mappings_dir / "sshd.json"), str(samples_dir / "sshd.log")])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.strip()]
    # sshd: 88 events after the regex filters "Invalid user" lines.
    assert len(lines) == 88
    assert json.loads(lines[0])["class_uid"] == 3002


def test_cli_apply_csv_sink_from_extension(tmp_path, mappings_dir, samples_dir, monkeypatch):
    monkeypatch.chdir(mappings_dir.parent)
    out = tmp_path / "out.csv"
    rc = main(["apply", str(mappings_dir / "okta.json"),
               str(samples_dir / "okta.jsonl"), str(out)])
    assert rc == 0
    text = out.read_text()
    header = text.splitlines()[0]
    assert "class_uid" in header
    assert "metadata.product.name" in header


def test_cli_apply_forced_sink_kind(tmp_path, mappings_dir, samples_dir, monkeypatch):
    monkeypatch.chdir(mappings_dir.parent)
    # `.foo` extension would default to jsonl; --sink forces csv.
    out = tmp_path / "out.foo"
    rc = main(["apply", str(mappings_dir / "okta.json"),
               str(samples_dir / "okta.jsonl"), str(out), "--sink", "csv"])
    assert rc == 0
    assert "class_uid" in out.read_text().splitlines()[0]


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_cli_validate_passes_on_authentic_events(tmp_path, mappings_dir, samples_dir, monkeypatch, capsys):
    monkeypatch.chdir(mappings_dir.parent)
    # Build a JSONL of authentication events from sshd's output.
    events_path = tmp_path / "auth.jsonl"
    main(["apply", str(mappings_dir / "sshd.json"), str(samples_dir / "sshd.log"), str(events_path)])
    capsys.readouterr()  # discard apply's status line

    rc = main(["validate", str(events_path), "authentication"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "FAILED: 0" in out


def test_cli_validate_flags_failures(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({}) + "\n")
    rc = main(["validate", str(bad), "authentication"])
    assert rc == 1
    assert "FAIL" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# list / catalog / lint forwarding
# ---------------------------------------------------------------------------


def test_cli_list_table(mappings_dir, monkeypatch, capsys):
    monkeypatch.chdir(mappings_dir.parent)
    rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NAME" in out and "PRIORITY" in out
    assert "cloudtrail" in out


def test_cli_list_json(mappings_dir, monkeypatch, capsys):
    monkeypatch.chdir(mappings_dir.parent)
    rc = main(["list", "--format", "json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert any(r["name"] == "cloudtrail" for r in rows)


def test_cli_catalog(repo_root, monkeypatch, capsys):
    monkeypatch.chdir(repo_root)
    rc = main(["catalog"])
    assert rc == 0
    assert "LOG SOURCE" in capsys.readouterr().out


def test_cli_lint(mappings_dir, monkeypatch, capsys):
    monkeypatch.chdir(mappings_dir.parent)
    rc = main(["lint", str(mappings_dir)])
    assert rc == 0
    assert "OVERALL: PASS" in capsys.readouterr().out
