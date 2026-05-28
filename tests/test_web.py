"""Tests for the FastAPI web UI."""

from __future__ import annotations

from pathlib import Path

import pytest


fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ocsf_mapper.web import create_app  # noqa: E402


@pytest.fixture
def client(repo_root, monkeypatch):
    monkeypatch.chdir(repo_root)
    return TestClient(create_app(root=repo_root))


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["schema_version"] == "1.9.0-dev"


def test_homepage_lists_all_sources(client):
    r = client.get("/")
    assert r.status_code == 200
    # Cards for a sampling of source display names — confirms catalog wiring works.
    for needle in ("Windows Event Log", "AWS CloudTrail", "Okta", "Cloudflare", "Sysmon"):
        assert needle in r.text, f"homepage missing card for {needle!r}"
    # Card grid + priority badges should render.
    assert "card-grid" in r.text
    assert "pri-critical" in r.text


def test_source_page_renders(client):
    r = client.get("/sources/cloudtrail")
    assert r.status_code == 200
    assert "AWS CloudTrail" in r.text
    assert "tabs" in r.text  # the tab strip
    # Sample tab loads via HTMX hx-get — verify the partial endpoint works too.
    r2 = client.get("/sources/cloudtrail/sample")
    assert r2.status_code == 200
    assert "sample-pre" in r2.text


def test_source_page_404_for_unknown(client):
    r = client.get("/sources/this_does_not_exist")
    assert r.status_code == 404


def test_apply_uploaded_file_returns_ocsf_output(client, samples_dir):
    sample_bytes = (samples_dir / "okta.jsonl").read_bytes()
    r = client.post(
        "/sources/okta/apply",
        files={"file": ("okta.jsonl", sample_bytes, "application/x-ndjson")},
    )
    assert r.status_code == 200
    assert "output-result" in r.text
    assert "authentication" in r.text or "entity_management" in r.text
    # Should report 100 events from the 100-line sample.
    assert "<strong>100</strong>" in r.text


def test_apply_handles_regex_misses_gracefully(client, tmp_path, samples_dir):
    # The sshd parser is regex-based; non-matching lines get reported as "no match",
    # not 500s. Send a file with one matching + one bogus line.
    payload = b"this line will not match\nMay 27 14:23:11 host sshd[1]: Accepted password for alice from 10.0.0.1 port 1234 ssh2\n"
    r = client.post(
        "/sources/sshd/apply",
        files={"file": ("mixed.log", payload, "text/plain")},
    )
    assert r.status_code == 200
    assert "no match" in r.text
    assert "authentication" in r.text


def test_static_files_served(client):
    r = client.get("/static/main.css")
    assert r.status_code == 200
    assert "card-grid" in r.text or "badge" in r.text


# ---------------------------------------------------------------------------
# Step 1: Mapping editor tab + save with server-side lint
# ---------------------------------------------------------------------------


def test_mapping_editor_partial_loads(client):
    r = client.get("/sources/cloudtrail/mapping")
    assert r.status_code == 200
    assert "monaco-host" in r.text
    assert "cloudtrail" in r.text
    assert "Save" in r.text


def test_save_rejects_invalid_json(client):
    r = client.post("/sources/cloudtrail/save", data={"content": "{not valid"})
    assert r.status_code == 400
    assert "invalid JSON" in r.text


def test_save_rejects_lint_failure(client):
    import json as _j
    bad = {
        "parser": "json",
        "classes": {
            "authentication": {"mapping": {"class_uid": {"const": 3002}}}
        },
    }
    r = client.post("/sources/cloudtrail/save", data={"content": _j.dumps(bad)})
    assert r.status_code == 400
    assert "Save rejected" in r.text


def test_save_accepts_clean_mapping(tmp_path, repo_root):
    """Isolated copy of repo so we don't mutate the real mappings/."""
    import shutil, json as _j
    isolated = tmp_path / "repo"
    shutil.copytree(repo_root / "mappings", isolated / "mappings")
    shutil.copytree(repo_root / "samples", isolated / "samples")
    shutil.copy(repo_root / "catalog.json", isolated / "catalog.json")
    iso_client = TestClient(create_app(root=isolated))

    current = _j.loads((isolated / "mappings/okta.json").read_text())
    r = iso_client.post("/sources/okta/save", data={"content": _j.dumps(current)})
    assert r.status_code == 200, r.text
    assert "Saved" in r.text
    assert _j.loads((isolated / "mappings/okta.json").read_text())["source_name"] == "okta"


def test_save_404_for_unknown_source(client):
    r = client.post("/sources/totally_made_up/save", data={"content": "{}"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Step 2: Validation tab
# ---------------------------------------------------------------------------


def test_validation_tab_clean_sample(client):
    r = client.get("/sources/cloudtrail/validation")
    assert r.status_code == 200
    assert "validation-summary" in r.text
    assert "<strong>100</strong>" in r.text
    assert "100 valid" in r.text


def test_validation_tab_surfaces_recurring_issues(tmp_path, repo_root):
    """When a mapping is broken in a uniform way, the validation tab should
    aggregate the same error across many events under 'Recurring issues'."""
    import shutil, json as _j
    isolated = tmp_path / "repo"
    shutil.copytree(repo_root / "mappings", isolated / "mappings")
    shutil.copytree(repo_root / "samples", isolated / "samples")
    shutil.copy(repo_root / "catalog.json", isolated / "catalog.json")

    okta = _j.loads((isolated / "mappings/okta.json").read_text())
    for cls in okta["classes"].values():
        cls["mapping"].pop("severity_id", None)
        cls["mapping"].pop("severity", None)
    (isolated / "mappings/okta.json").write_text(_j.dumps(okta))

    iso_client = TestClient(create_app(root=isolated))
    r = iso_client.get("/sources/okta/validation")
    assert r.status_code == 200
    assert "failing" in r.text
    assert "Recurring issues" in r.text
    assert "severity_id" in r.text


def test_validation_tab_missing_sample(tmp_path):
    (tmp_path / "mappings").mkdir()
    (tmp_path / "samples").mkdir()
    (tmp_path / "mappings/orphan.json").write_text('{"parser":"json","classes":{}}')
    (tmp_path / "catalog.json").write_text(
        '{"ocsf_schema_version":"1.9.0-dev","entries":[' +
        '{"source":"orphan","display_name":"X","vendor":"X","priority":"low","description":"",' +
        '"ocsf":{"category_uid":1,"category_name":"X","class_uid":1001,"class_name":"X"}}]}'
    )
    iso_client = TestClient(create_app(root=tmp_path))
    r = iso_client.get("/sources/orphan/validation")
    assert r.status_code == 200
    assert "No pinned sample" in r.text
