"""End-to-end tests for the generator using FixtureProvider."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ocsf_mapper.generate import (
    _strip_codefence,
    draft_mapping,
    generate,
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
