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


def test_mapping_editor_lists_schema_version_options(client):
    """The Mapping tab dropdown should list every available pinned schema
    version (the current submodule plus any ocsf-schema-<v>/ worktrees)."""
    r = client.get("/sources/cloudtrail/mapping")
    assert r.status_code == 200
    assert "schema-version-select" in r.text
    # Default (current submodule) version.
    assert "1.9.0-dev" in r.text
    # Pinned alternates from scripts/setup_schema_versions.sh.
    assert "1.8.0" in r.text


def test_save_against_pinned_schema_version_reports_it(client):
    """A rejection rendered while linting against a non-default schema
    version names that version so the user knows which schema the gate ran."""
    bad = "{not valid"
    r = client.post(
        "/sources/cloudtrail/save",
        data={"content": bad, "schema_version": "1.8.0"},
    )
    assert r.status_code == 400
    assert "OCSF 1.8.0" in r.text


def test_save_against_missing_version_returns_clear_error(client):
    r = client.post(
        "/sources/cloudtrail/save",
        data={"content": "{}", "schema_version": "99.99.99"},
    )
    assert r.status_code == 400
    assert "setup_schema_versions.sh" in r.text


# ---------------------------------------------------------------------------
# Fix-with-AI endpoint
# ---------------------------------------------------------------------------


def test_fix_with_ai_returns_repaired_mapping(client, monkeypatch, repo_root):
    """End-to-end: post a broken mapping, the configured FixtureProvider
    returns a fixed mapping, the endpoint surfaces it as JSON."""
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv(
        "OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"),
    )
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "cloudtrail_fix")

    # Valid JSON but missing required attrs — lints fail, then AI fixes.
    broken = (
        '{"parser":"json",'
        '"classes":{"authentication":{"mapping":{"class_uid":{"const":3002}}}}}'
    )
    r = client.post("/sources/cloudtrail/fix-with-ai", data={"content": broken})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["provider"] == "fixture"
    assert body["n_errors_fixed"] >= 1
    # The returned `mapping` is a JSON string the frontend will load into Monaco.
    import json as _j
    parsed = _j.loads(body["mapping"])
    assert isinstance(parsed, dict)
    assert "classes" in parsed or "source_name" in parsed


def test_fix_with_ai_rejects_invalid_json(client, monkeypatch, repo_root):
    """If the editor buffer isn't valid JSON, we fail fast — no point
    burning an LLM call on something even the parser can't handle."""
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv(
        "OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"),
    )
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "cloudtrail_fix")

    r = client.post(
        "/sources/cloudtrail/fix-with-ai",
        data={"content": "{not valid json"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert "invalid JSON" in body["error"]


def test_fix_with_ai_returns_503_when_no_provider_configured(client, monkeypatch):
    """Without a key (and no fixture pointer), the endpoint surfaces a
    503 plus a clear setup hint instead of throwing — the UI shows it
    as a friendly notice next to the button."""
    monkeypatch.delenv("OCSF_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    broken = (
        '{"parser":"json",'
        '"classes":{"authentication":{"mapping":{"class_uid":{"const":3002}}}}}'
    )
    r = client.post("/sources/cloudtrail/fix-with-ai", data={"content": broken})
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["code"] == "no_provider"
    assert "ANTHROPIC_API_KEY" in body["error"] or "OPENAI_API_KEY" in body["error"]


def test_fix_with_ai_404_for_unknown_source(client):
    r = client.post(
        "/sources/totally_made_up/fix-with-ai", data={"content": "{}"},
    )
    assert r.status_code == 404


def test_regenerate_with_ai_returns_fresh_draft(client, monkeypatch, repo_root):
    """End-to-end regenerate: POST hits the two-phase generator via the
    FixtureProvider and returns the canned mapping as a JSON string."""
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv(
        "OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"),
    )
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "cloudtrail_regen")

    r = client.post("/sources/cloudtrail/regenerate-with-ai")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["provider"] == "fixture"
    # Same shape as fix-with-ai — frontend stuffs body["mapping"] into Monaco.
    import json as _j
    parsed = _j.loads(body["mapping"])
    assert isinstance(parsed, dict)


def test_regenerate_with_ai_second_call_hits_cache(monkeypatch, tmp_path, repo_root):
    """Identical Regenerate inputs return the previous draft from the in-memory
    LRU instead of re-calling the LLM. ``cached: true`` flag signals the UI
    so it can tell the user no API call was made."""
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv(
        "OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"),
    )
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "cloudtrail_regen")

    # Fresh app instance so the cache starts empty (the session-scoped
    # ``client`` fixture would share state across tests).
    import shutil
    from fastapi.testclient import TestClient
    isolated = tmp_path / "repo"
    shutil.copytree(repo_root / "mappings", isolated / "mappings")
    shutil.copytree(repo_root / "samples", isolated / "samples")
    shutil.copy(repo_root / "catalog.json", isolated / "catalog.json")
    iso_client = TestClient(create_app(root=isolated))

    r1 = iso_client.post("/sources/cloudtrail/regenerate-with-ai")
    assert r1.status_code == 200, r1.text
    assert r1.json()["cached"] is False  # first call goes to fixture

    r2 = iso_client.post("/sources/cloudtrail/regenerate-with-ai")
    assert r2.status_code == 200
    assert r2.json()["cached"] is True   # second call served from LRU
    assert r2.json()["provider"] == "cache"
    # Same mapping returned — proves the cache value matches the live result.
    assert r2.json()["mapping"] == r1.json()["mapping"]


def test_fix_with_ai_second_call_hits_cache(monkeypatch, tmp_path, repo_root):
    """Same content + schema_version + source → second Fix-with-AI returns
    cached output, no LLM call, no token spend."""
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv(
        "OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"),
    )
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "cloudtrail_fix")

    import shutil
    from fastapi.testclient import TestClient
    isolated = tmp_path / "repo"
    shutil.copytree(repo_root / "mappings", isolated / "mappings")
    shutil.copytree(repo_root / "samples", isolated / "samples")
    shutil.copy(repo_root / "catalog.json", isolated / "catalog.json")
    iso_client = TestClient(create_app(root=isolated))

    broken = (
        '{"parser":"json",'
        '"classes":{"authentication":{"mapping":{"class_uid":{"const":3002}}}}}'
    )
    r1 = iso_client.post("/sources/cloudtrail/fix-with-ai", data={"content": broken})
    assert r1.status_code == 200
    assert r1.json()["cached"] is False

    r2 = iso_client.post("/sources/cloudtrail/fix-with-ai", data={"content": broken})
    assert r2.status_code == 200
    assert r2.json()["cached"] is True
    assert r2.json()["mapping"] == r1.json()["mapping"]


def test_regenerate_with_ai_no_provider_503(client, monkeypatch):
    monkeypatch.delenv("OCSF_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.post("/sources/cloudtrail/regenerate-with-ai")
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "no_provider"


def test_regenerate_with_ai_404_for_unknown_source(client):
    r = client.post("/sources/totally_made_up/regenerate-with-ai")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Suggest-improvements endpoint
# ---------------------------------------------------------------------------


def test_suggest_improvements_returns_expanded_mapping(client, monkeypatch, repo_root):
    """Happy path: post a lint-clean but incomplete-coverage mapping, the
    LLM returns an expanded version with additional field mappings."""
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv(
        "OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"),
    )
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "cloudtrail_suggest")

    # Use the on-disk cloudtrail.json — it lints clean but coverage isn't
    # 100% for every recommended attr, so the endpoint will call the LLM.
    import json as _j
    current = (repo_root / "mappings" / "cloudtrail.json").read_text()
    r = client.post(
        "/sources/cloudtrail/suggest-improvements",
        data={"content": current},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["provider"] == "fixture"
    assert body["missing_attrs"] >= 1  # cloudtrail has at least one uncovered
    parsed = _j.loads(body["mapping"])
    assert isinstance(parsed, dict)


def test_suggest_improvements_short_circuits_when_complete(client, monkeypatch, repo_root):
    """If coverage is 100%, the endpoint returns a "nothing to improve"
    message without burning an LLM call. We can't easily construct a
    100%-coverage mapping in a test, so we verify the inverse: a mapping
    with KNOWN missing attrs reports missing_attrs > 0."""
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv(
        "OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"),
    )
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "cloudtrail_suggest")

    # A minimal mapping that doesn't declare any classes → coverage report
    # is empty → n_missing == 0 → short-circuit fires.
    r = client.post(
        "/sources/cloudtrail/suggest-improvements",
        data={"content": '{"parser":"json","classes":{}}'},
    )
    assert r.status_code == 400
    body = r.json()
    assert "Nothing to improve" in body["error"]


def test_suggest_improvements_rejects_invalid_json(client, monkeypatch, repo_root):
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv(
        "OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"),
    )
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "cloudtrail_suggest")
    r = client.post(
        "/sources/cloudtrail/suggest-improvements",
        data={"content": "{not valid json"},
    )
    assert r.status_code == 400
    assert "invalid JSON" in r.json()["error"]


def test_suggest_improvements_no_provider_503(client, monkeypatch, repo_root):
    monkeypatch.delenv("OCSF_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    current = (repo_root / "mappings" / "cloudtrail.json").read_text()
    r = client.post(
        "/sources/cloudtrail/suggest-improvements",
        data={"content": current},
    )
    assert r.status_code == 503
    assert r.json()["code"] == "no_provider"


def test_suggest_improvements_404_for_unknown_source(client):
    r = client.post(
        "/sources/totally_made_up/suggest-improvements",
        data={"content": "{}"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Coverage delta — each AI flow reports before / after scores so the UI
# can render "coverage 67% → 89%". Scores are floats in [0, 1] or null
# when the mapping doesn't declare any OCSF classes (delta undefined).
# ---------------------------------------------------------------------------


def test_fix_with_ai_response_includes_coverage_delta(client, monkeypatch, repo_root):
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv(
        "OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"),
    )
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "cloudtrail_fix")
    broken = (
        '{"parser":"json",'
        '"classes":{"authentication":{"mapping":{"class_uid":{"const":3002}}}}}'
    )
    r = client.post("/sources/cloudtrail/fix-with-ai", data={"content": broken})
    assert r.status_code == 200
    body = r.json()
    assert "before_score" in body
    assert "after_score" in body
    # The "fixed" response is cloudtrail.json — known to be high-coverage.
    assert isinstance(body["after_score"], float) and 0.0 <= body["after_score"] <= 1.0


def test_regenerate_with_ai_response_includes_coverage_delta(client, monkeypatch, repo_root):
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv(
        "OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"),
    )
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "cloudtrail_regen")
    r = client.post("/sources/cloudtrail/regenerate-with-ai")
    assert r.status_code == 200
    body = r.json()
    assert "before_score" in body
    assert "after_score" in body
    # Before = the on-disk cloudtrail.json (clean, high-coverage).
    assert isinstance(body["before_score"], float)


def test_suggest_improvements_response_includes_coverage_delta(client, monkeypatch, repo_root):
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv(
        "OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"),
    )
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "cloudtrail_suggest")
    current = (repo_root / "mappings" / "cloudtrail.json").read_text()
    r = client.post(
        "/sources/cloudtrail/suggest-improvements", data={"content": current},
    )
    assert r.status_code == 200
    body = r.json()
    assert "before_score" in body
    assert "after_score" in body
    assert isinstance(body["before_score"], float)
    assert isinstance(body["after_score"], float)


def test_fix_with_ai_rejects_clean_mapping(client, monkeypatch, repo_root):
    """Nothing-to-fix path: if the user clicks the button while the
    current buffer already lints clean, return a clear message instead
    of burning an LLM call."""
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv(
        "OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"),
    )
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "cloudtrail_fix")

    import json as _j
    current = (repo_root / "mappings" / "cloudtrail.json").read_text()
    r = client.post("/sources/cloudtrail/fix-with-ai", data={"content": current})
    assert r.status_code == 400
    body = r.json()
    assert "Nothing to fix" in body["error"]


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


# ---------------------------------------------------------------------------
# Step 3: Coverage bars on home + dedicated Coverage tab
# ---------------------------------------------------------------------------


def test_snippets_tab_renders(client):
    """Snippets tab serves a partial with CLI / Python SDK / PySpark / Pandas
    blocks templated with the actual mapping + sample paths."""
    r = client.get("/sources/cloudtrail/snippets")
    assert r.status_code == 200
    body = r.text
    # Each snippet header label.
    for label in ("CLI", "Python (SDK)", "PySpark (UDF)", "Pandas"):
        assert label in body, f"snippets partial missing {label!r}"
    # Per-mapping templating: the cloudtrail paths land in the snippets.
    assert "mappings/cloudtrail.json" in body
    assert "samples/cloudtrail.jsonl" in body
    # SDK call should be present in the Python block.
    assert "apply_stream_with_class" in body
    # Spark broadcast pattern in the PySpark block.
    assert "sparkContext.broadcast" in body
    # Copy button wiring.
    assert "snippet-copy" in body


def test_snippets_tab_404_for_unknown_source(client):
    r = client.get("/sources/this_does_not_exist/snippets")
    assert r.status_code == 404


def test_source_page_lists_snippets_tab(client):
    r = client.get("/sources/cloudtrail")
    assert r.status_code == 200
    assert "Snippets" in r.text
    assert "/sources/cloudtrail/snippets" in r.text


def test_homepage_includes_coverage_bars(client):
    import re
    r = client.get("/")
    assert r.status_code == 200
    assert "card-coverage" in r.text
    assert "bar-req" in r.text
    assert re.search(r"\d+%</span>", r.text)


def test_coverage_tab_renders(client):
    r = client.get("/sources/cloudtrail/coverage")
    assert r.status_code == 200
    assert "coverage-overall" in r.text
    assert "authentication" in r.text and "api_activity" in r.text
    assert "Missing recommended" in r.text


def test_coverage_tab_404_for_unknown(client):
    r = client.get("/sources/totally_made_up/coverage")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Step 4: New-source wizard
# ---------------------------------------------------------------------------


@pytest.fixture
def wizard_env(tmp_path, monkeypatch, repo_root):
    """Isolated repo + fixture LLM, so the wizard can run without API access."""
    (tmp_path / "mappings").mkdir()
    (tmp_path / "samples").mkdir()
    (tmp_path / "catalog.json").write_text('{"ocsf_schema_version":"1.9.0-dev","entries":[]}')
    monkeypatch.setenv("OCSF_LLM_PROVIDER", "fixture")
    monkeypatch.setenv("OCSF_LLM_FIXTURE_DIR", str(repo_root / "tests" / "fixtures" / "llm"))
    monkeypatch.setenv("OCSF_LLM_FIXTURE_SOURCE", "my_test_source")
    return TestClient(create_app(root=tmp_path)), tmp_path


def test_wizard_landing_renders(client):
    r = client.get("/new")
    assert r.status_code == 200
    assert "Source name" in r.text
    assert "wizard-form" in r.text


def test_wizard_draft_then_save_writes_mapping(wizard_env):
    import html as _html, re as _re
    iso_client, root = wizard_env

    sample = b'{"event_type":"login","ts":"2026-05-27T14:23:11Z","user":"alice"}\n'
    r = iso_client.post(
        "/new/draft",
        data={"source_name": "my_test_source", "vendor": "Acme",
              "priority": "medium", "description": "demo"},
        files={"sample": ("demo.jsonl", sample, "application/x-ndjson")},
    )
    assert r.status_code == 200
    assert "Draft mapping for" in r.text

    # Extract the draft JSON the editor was preloaded with.
    m = _re.search(r'data-initial="([^"]+)"', r.text)
    assert m, "draft JSON not found in editor partial"
    draft = _html.unescape(m.group(1))

    r2 = iso_client.post("/new/save",
                          data={"source_name": "my_test_source", "content": draft})
    assert r2.status_code == 200, r2.text
    assert "Saved" in r2.text
    assert (root / "mappings/my_test_source.json").exists()
    # Sample was placed in samples/ under the source name.
    assert (root / "samples/my_test_source.jsonl").exists()


def test_wizard_draft_rejects_invalid_source_name(wizard_env):
    iso_client, _ = wizard_env
    r = iso_client.post(
        "/new/draft",
        data={"source_name": "Bad Name With Spaces", "vendor": "Acme", "priority": "low"},
        files={"sample": ("x.jsonl", b"{}\n", "application/x-ndjson")},
    )
    assert r.status_code == 400
    assert "Invalid source_name" in r.text


def test_wizard_save_refuses_to_overwrite(wizard_env):
    iso_client, root = wizard_env
    (root / "mappings/my_test_source.json").write_text("{}")
    r = iso_client.post("/new/save",
                        data={"source_name": "my_test_source", "content": "{}"})
    assert r.status_code == 409
    assert "already exists" in r.text


def test_wizard_save_rejects_bad_json(wizard_env):
    iso_client, _ = wizard_env
    r = iso_client.post("/new/save",
                        data={"source_name": "my_test_source", "content": "not json"})
    assert r.status_code == 400
    assert "invalid JSON" in r.text


# ---------------------------------------------------------------------------
# Step 5: Live-tail SSE endpoint
# ---------------------------------------------------------------------------


def test_tail_missing_file_returns_404(client):
    r = client.get("/sources/okta/tail", params={"file": "/does/not/exist.log"})
    assert r.status_code == 404


def test_tail_unknown_source_returns_404(client, samples_dir):
    r = client.get(
        "/sources/no_such_source/tail",
        params={"file": str(samples_dir / "okta.jsonl")},
    )
    assert r.status_code == 404


def test_tail_streams_events_from_existing_file(client, samples_dir):
    import json as _json

    sample_path = str(samples_dir / "okta.jsonl")
    r = client.get(
        "/sources/okta/tail",
        params={"file": sample_path, "from_start": "true", "max_events": "3"},
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]

    events = [
        _json.loads(line[6:])
        for line in r.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(events) == 3
    first = events[0]
    assert "raw" in first
    assert "event" in first or "error" in first


def test_tail_output_tab_template_present(client):
    r = client.get("/sources/okta")
    assert r.status_code == 200
    assert "tail-section" in r.text
    assert "tail-controls" in r.text
    assert "tailStart" in r.text


def test_wizard_draft_friendly_error_without_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("OCSF_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / "mappings").mkdir(); (tmp_path / "samples").mkdir()
    (tmp_path / "catalog.json").write_text('{"ocsf_schema_version":"1.9.0-dev","entries":[]}')
    iso_client = TestClient(create_app(root=tmp_path))
    r = iso_client.post(
        "/new/draft",
        data={"source_name": "demo", "vendor": "Acme", "priority": "medium"},
        files={"sample": ("x.jsonl", b'{"x":1}\n', "application/x-ndjson")},
    )
    assert r.status_code == 500
    assert "ANTHROPIC_API_KEY" in r.text or "fixture" in r.text
