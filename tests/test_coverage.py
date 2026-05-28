"""Tests for ocsf_mapper.coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ocsf_mapper.coverage import coverage, summary


def test_coverage_required_complete_for_cloudtrail(mappings_dir, schema):
    cfg = json.loads((mappings_dir / "cloudtrail.json").read_text())
    cov = coverage(cfg, schema)
    assert set(cov.keys()) == {"authentication", "api_activity"}
    # Reference mappings are designed to populate all required attrs.
    for cls_cov in cov.values():
        assert cls_cov["required"] == cls_cov["required_total"]
        assert cls_cov["missing_required"] == []


def test_coverage_flags_missing_required(mappings_dir, schema):
    cfg = json.loads((mappings_dir / "cloudtrail.json").read_text())
    # Drop `metadata.product.name` etc. — anything not under one of:
    for cls in cfg["classes"].values():
        cls["mapping"] = {k: v for k, v in cls["mapping"].items() if not k.startswith("metadata")}
    cov = coverage(cfg, schema)
    auth = cov["authentication"]
    assert "metadata" in auth["missing_required"]
    assert auth["required"] < auth["required_total"]


def test_coverage_score_weights_required_twice(schema):
    cfg = {
        "classes": {
            "authentication": {
                "mapping": {
                    # Hit all required (7 attrs)
                    "metadata.version": {"const": "1.0"},
                    "time": {"const": 1},
                    "category_uid": {"const": 3},
                    "class_uid": {"const": 3002},
                    "severity_id": {"const": 1},
                    "type_uid": {"const": 300201},
                    "user.name": {"const": "x"},
                }
            }
        }
    }
    cov = coverage(cfg, schema)
    s = summary(cov)
    # All required hit, no recommended → required dominates the score.
    assert s["required"] == s["required_total"]
    # Score = (req_h*2 + rec_h) / (req_t*2 + rec_t) — required weighted 2x.
    expected = (s["required"] * 2 + s["recommended"]) / (s["required_total"] * 2 + s["recommended_total"])
    assert s["score"] == pytest.approx(expected)


def test_coverage_skips_unknown_classes(schema):
    cfg = {"classes": {"definitely_not_a_real_class": {"mapping": {}}}}
    assert coverage(cfg, schema) == {}


def test_summary_handles_empty_dict():
    s = summary({})
    assert s["score"] == 1.0
    assert s["required"] == s["required_total"] == 0
