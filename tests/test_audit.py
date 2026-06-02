"""Tests for the audit log + audit-aware save endpoints + /metrics."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ocsf_mapper.audit import audit_path, log_edit, read_audit


# ---------------------------------------------------------------------------
# audit module — direct
# ---------------------------------------------------------------------------


def test_log_edit_appends_one_jsonl_line(tmp_path):
    log_edit(tmp_path, mapping="x", action="update",
             lint_status="OK", bytes_before=10, bytes_after=20)
    log_edit(tmp_path, mapping="y", action="create",
             lint_status="FAIL", errors=["bad"], bytes_after=30)
    text = audit_path(tmp_path).read_text()
    lines = text.splitlines()
    assert len(lines) == 2
    e0 = json.loads(lines[0])
    e1 = json.loads(lines[1])
    assert e0["mapping"] == "x"
    assert e0["lint_status"] == "OK"
    assert e1["mapping"] == "y"
    assert e1["errors"] == ["bad"]


def test_log_edit_user_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("OCSF_AUDIT_USER", "alice")
    log_edit(tmp_path, mapping="x", action="update", lint_status="OK")
    e = json.loads(audit_path(tmp_path).read_text().strip())
    assert e["user"] == "alice"


def test_log_edit_user_falls_back_to_local(monkeypatch, tmp_path):
    monkeypatch.delenv("OCSF_AUDIT_USER", raising=False)
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    log_edit(tmp_path, mapping="x", action="update", lint_status="OK")
    e = json.loads(audit_path(tmp_path).read_text().strip())
    assert e["user"] == "local"


def test_read_audit_returns_newest_first(tmp_path):
    log_edit(tmp_path, mapping="first", action="update", lint_status="OK")
    log_edit(tmp_path, mapping="second", action="update", lint_status="OK")
    log_edit(tmp_path, mapping="third", action="update", lint_status="OK")
    events = read_audit(tmp_path)
    assert [e["mapping"] for e in events] == ["third", "second", "first"]


def test_read_audit_respects_limit(tmp_path):
    for i in range(5):
        log_edit(tmp_path, mapping=f"m-{i}", action="update", lint_status="OK")
    assert len(read_audit(tmp_path, limit=2)) == 2


def test_read_audit_empty_when_missing(tmp_path):
    assert read_audit(tmp_path) == []


# ---------------------------------------------------------------------------
# web hooks — save endpoint writes audit events
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_repo(tmp_path, repo_root):
    """Copy mappings/samples/catalog to tmp_path so saves don't pollute the repo."""
    shutil.copytree(repo_root / "mappings", tmp_path / "mappings")
    shutil.copytree(repo_root / "samples", tmp_path / "samples")
    shutil.copy(repo_root / "catalog.json", tmp_path / "catalog.json")
    return tmp_path


def test_save_writes_audit_event(isolated_repo):
    from fastapi.testclient import TestClient
    from ocsf_mapper.web import create_app
    iso_client = TestClient(create_app(root=isolated_repo))
    current = json.loads((isolated_repo / "mappings/okta.json").read_text())
    r = iso_client.post("/sources/okta/save",
                        data={"content": json.dumps(current)})
    assert r.status_code == 200
    events = read_audit(isolated_repo)
    assert events
    e = events[0]
    assert e["mapping"] == "okta"
    assert e["action"] == "update"
    assert e["lint_status"] == "OK"


def test_save_invalid_json_writes_rejected_audit(isolated_repo):
    from fastapi.testclient import TestClient
    from ocsf_mapper.web import create_app
    iso_client = TestClient(create_app(root=isolated_repo))
    r = iso_client.post("/sources/okta/save",
                        data={"content": "{not json"})
    assert r.status_code == 400
    events = read_audit(isolated_repo)
    assert events
    e = events[0]
    assert e["lint_status"] == "REJECTED"
    assert any("invalid JSON" in err for err in e["errors"])


def test_audit_page_renders(isolated_repo):
    from fastapi.testclient import TestClient
    from ocsf_mapper.web import create_app
    iso_client = TestClient(create_app(root=isolated_repo))
    # No events yet
    r = iso_client.get("/audit")
    assert r.status_code == 200
    assert "No audit events" in r.text
    # Trigger one save, then refresh
    current = json.loads((isolated_repo / "mappings/okta.json").read_text())
    iso_client.post("/sources/okta/save", data={"content": json.dumps(current)})
    r = iso_client.get("/audit")
    assert r.status_code == 200
    assert "okta" in r.text
    assert "✓ saved" in r.text


# ---------------------------------------------------------------------------
# /metrics
# ---------------------------------------------------------------------------


def test_metrics_endpoint_exposes_counts(isolated_repo):
    from fastapi.testclient import TestClient
    from ocsf_mapper.web import create_app
    iso_client = TestClient(create_app(root=isolated_repo))
    r = iso_client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert "ocsf_mappings_total " in body
    assert "ocsf_mappings_lint_ok " in body
    assert "ocsf_mappings_coverage_avg " in body
    # Counter starts at 0 with no edits.
    assert "ocsf_mapping_edits_total 0" in body
    # Schema version label always present.
    assert 'ocsf_schema_version_info{version=' in body


def test_metrics_counts_audit_events(isolated_repo):
    from fastapi.testclient import TestClient
    from ocsf_mapper.web import create_app
    iso_client = TestClient(create_app(root=isolated_repo))
    current = json.loads((isolated_repo / "mappings/okta.json").read_text())
    iso_client.post("/sources/okta/save", data={"content": json.dumps(current)})
    iso_client.post("/sources/okta/save", data={"content": "{not json"})
    r = iso_client.get("/metrics")
    body = r.text
    assert "ocsf_mapping_edits_total 2" in body
    assert "ocsf_mapping_edits_saved_total 1" in body
    assert "ocsf_mapping_edits_rejected_total 1" in body
