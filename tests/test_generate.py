"""End-to-end tests for the generator using FixtureProvider."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ocsf_mapper.generate import (
    _strip_codefence,
    draft_mapping,
    fix_mapping,
    generate,
    prompt_fix,
    suggest_classes,
)
from ocsf_mapper.providers import FixtureProvider


@pytest.fixture
def fixture_provider(repo_root):
    return FixtureProvider(fixture_dir=repo_root / "tests" / "fixtures" / "llm",
                            source="demo_source")


@pytest.fixture
def demo_sample(tmp_path):
    p = tmp_path / "demo.jsonl"
    p.write_text('{"event_type":"login","ts":"2026-05-27T14:23:11Z","user":"alice"}\n')
    return p


# ---------------------------------------------------------------------------
# suggest_classes
# ---------------------------------------------------------------------------


def test_suggest_classes_parses_llm_json(fixture_provider, schema):
    routing = suggest_classes(["{\"event_type\":\"login\"}"], "demo_source",
                              provider=fixture_provider, schema=schema)
    assert routing["routing_field"] == "$.event_type"
    assert routing["classes"][0]["ocsf_class_name"] == "authentication"


def test_suggest_classes_rejects_unknown_ocsf_class(schema, tmp_path):
    (tmp_path / "bad.json").write_text(json.dumps([
        json.dumps({"routing_field": "$.x", "classes": [{"ocsf_class_name": "not_a_real_class"}]})
    ]))
    p = FixtureProvider(fixture_dir=tmp_path, source="bad")
    with pytest.raises(ValueError) as exc:
        suggest_classes(["{}"], "x", provider=p, schema=schema)
    assert "unknown OCSF class" in str(exc.value)


# ---------------------------------------------------------------------------
# generate end-to-end
# ---------------------------------------------------------------------------


def test_generate_produces_full_config(fixture_provider, demo_sample, schema):
    cfg = generate(demo_sample, "demo_source",
                    provider=fixture_provider, schema=schema)
    assert cfg["source_name"] == "demo_source"
    assert cfg["parser"] == "json"
    assert "authentication" in cfg["classes"]
    # Sanity-check: the generated mapping should be runnable against the sample.
    from ocsf_mapper import apply_stream_with_class
    events = list(apply_stream_with_class(cfg, demo_sample.read_text().splitlines()))
    assert events
    assert events[0][0]["class_uid"] == 3002


def test_draft_mapping_called_with_classes_only(fixture_provider, schema):
    # First consume phase-1 response so phase-2 is next on the fixture cursor
    suggest_classes(["{}"], "demo_source", provider=fixture_provider, schema=schema)
    routing = {"routing_field": "$.x", "classes": [{"ocsf_class_name": "authentication"}]}
    cfg = draft_mapping(["{}"], "demo_source", routing,
                         provider=fixture_provider, schema=schema)
    assert "classes" in cfg


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_strip_codefence_handles_json_fence():
    s = "```json\n{\"a\": 1}\n```"
    assert _strip_codefence(s) == '{"a": 1}'


def test_strip_codefence_no_fence_passthrough():
    assert _strip_codefence("{\"a\": 1}") == '{"a": 1}'


# ---------------------------------------------------------------------------
# fix_mapping — Mapping-tab "Fix with AI" flow
# ---------------------------------------------------------------------------


def test_prompt_fix_includes_errors_and_schema_context(schema):
    """The fix prompt must surface both the linter errors and the required-
    attribute list for the target class, so the LLM can repair specifically
    what's broken without rewriting working sections."""
    current = {
        "parser": "json",
        "classes": {
            "authentication": {"mapping": {"class_uid": {"const": 3002}}}
        },
    }
    errors = ["event #1 (authentication): missing required attribute: category_uid"]
    prompt = prompt_fix(current, errors, ["{\"event_type\":\"login\"}"], schema)
    assert "category_uid" in prompt
    assert "FIXED" in prompt
    assert "authentication" in prompt
    # Required-attr list (we filter to required/recommended) — auth_protocol
    # is recommended on the Authentication class, so it should be in scope.
    assert "auth_protocol" in prompt


def test_fix_mapping_parses_llm_response(repo_root, schema):
    """End-to-end via FixtureProvider: a broken mapping in, a parsed dict
    out. The fixture canned response is the current cloudtrail config."""
    provider = FixtureProvider(
        fixture_dir=repo_root / "tests" / "fixtures" / "llm",
        source="cloudtrail_fix",
    )
    broken = {"parser": "json", "classes": {"authentication": {"mapping": {}}}}
    errors = ["event #1 (authentication): missing required attribute: category_uid"]
    result = fix_mapping(broken, errors, ["{}"], provider=provider, schema=schema)
    assert isinstance(result, dict)
    # The fixture returned cloudtrail; sanity-check it parsed as a mapping.
    assert "source_name" in result or "classes" in result


def test_fix_mapping_truncates_long_error_list(schema, fixture_provider):
    """Caps the error list at 30 lines in the prompt to keep token spend
    bounded; surplus errors collapse to a tail count."""
    current = {"parser": "json", "classes": {"authentication": {"mapping": {}}}}
    many = [f"event #{i}: missing required attribute: x" for i in range(50)]
    prompt = prompt_fix(current, many, [], schema)
    assert "elided" in prompt
    assert "20 more" in prompt
