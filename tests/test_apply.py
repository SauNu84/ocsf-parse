"""Orchestration tests for :mod:`ocsf_mapper.apply`."""

from __future__ import annotations

import json

import pytest

from ocsf_mapper import apply, apply_stream, apply_stream_with_class, apply_with_class
from ocsf_mapper.apply import map_record, parse_record, pick_class, prune


# ---------------------------------------------------------------------------
# parse_record
# ---------------------------------------------------------------------------


def test_parse_record_json_attaches_raw():
    rec = parse_record('{"a": 1}\n', "json")
    assert rec == {"a": 1, "__raw__": '{"a": 1}'}


def test_parse_record_regex_match_exposes_groups_at_top_level():
    spec = {"regex": r"^(?P<user>\w+) (?P<action>\w+)$", "groups": ["user", "action"]}
    rec = parse_record("alice login", spec)
    assert rec["user"] == "alice"
    assert rec["action"] == "login"
    assert rec["__groups__"] == {"user": "alice", "action": "login"}


def test_parse_record_regex_nomatch_returns_none():
    spec = {"regex": r"^X$", "groups": []}
    assert parse_record("nope", spec) is None


def test_parse_record_unknown_parser_raises():
    with pytest.raises(ValueError):
        parse_record("x", "lol")


# ---------------------------------------------------------------------------
# pick_class
# ---------------------------------------------------------------------------


def test_pick_class_no_routing_returns_first_class():
    assert pick_class({}, None, {"only_one": {}, "other": {}}) == "only_one"


def test_pick_class_matches_rule():
    routing = {"field": "$.kind", "rules": [{"matches": ["a"], "class": "ClassA"}]}
    assert pick_class({"kind": "a"}, routing, {"ClassA": {}}) == "ClassA"


def test_pick_class_prefix_rule():
    routing = {
        "field": "$.action",
        "rules": [
            {"matches": ["Create", "Add"], "prefix": True, "class": "CreateCls"},
            {"default": True, "class": "OtherCls"},
        ],
    }
    assert pick_class({"action": "CreateBucket"}, routing, {"CreateCls": {}, "OtherCls": {}}) == "CreateCls"


def test_pick_class_default_rule_wins_when_no_match():
    routing = {
        "field": "$.k",
        "rules": [
            {"matches": ["a"], "class": "A"},
            {"default": True, "class": "Fallback"},
        ],
    }
    assert pick_class({"k": "z"}, routing, {"A": {}, "Fallback": {}}) == "Fallback"


def test_pick_class_falls_back_to_default_class_in_routing():
    routing = {
        "field": "$.k",
        "rules": [{"matches": ["a"], "class": "A"}],
        "default_class": "DC",
    }
    assert pick_class({"k": "z"}, routing, {"A": {}, "DC": {}}) == "DC"


def test_pick_class_falls_back_to_first_class_when_nothing_matches():
    routing = {"field": "$.k", "rules": [{"matches": ["a"], "class": "A"}]}
    assert pick_class({"k": "z"}, routing, {"FirstByOrder": {}, "A": {}}) == "FirstByOrder"


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


def test_prune_drops_none_and_empties():
    src = {"a": 1, "b": None, "c": {}, "d": [], "e": {"f": None}, "g": [None, 2]}
    assert prune(src) == {"a": 1, "g": [2]}


def test_prune_keeps_zero_and_false():
    # Note: `if pv not in (None, {}, [])` keeps 0 and False because they're not in that tuple
    # (membership uses ==, but 0 != None and {} != False).
    src = {"a": 0, "b": False, "c": ""}
    out = prune(src)
    assert out["a"] == 0
    assert out["b"] is False
    # Empty string is not None / {} / [] so it survives:
    assert out["c"] == ""


def test_prune_passthrough_for_scalars():
    assert prune("hello") == "hello"


# ---------------------------------------------------------------------------
# map_record
# ---------------------------------------------------------------------------


def test_map_record_expr_uses_earlier_targets():
    block = {
        "mapping": {
            "class_uid": {"const": 3002},
            "activity_id": {"const": 1},
            "type_uid": {"expr": "class_uid * 100 + activity_id"},
        }
    }
    ev = map_record({}, block)
    assert ev["type_uid"] == 300201


def test_map_record_creates_nested_targets():
    block = {
        "mapping": {
            "metadata.product.name": {"const": "Test"},
            "user.uid": {"const": "u1"},
        }
    }
    ev = map_record({}, block)
    assert ev == {"metadata": {"product": {"name": "Test"}}, "user": {"uid": "u1"}}


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def test_apply_end_to_end(cloudtrail_config, cloudtrail_lines):
    ev = apply(cloudtrail_config, cloudtrail_lines[0])
    assert ev is not None
    assert ev["class_uid"] in {3002, 6003}
    assert "metadata" in ev


def test_apply_with_class_returns_pair(cloudtrail_config, cloudtrail_lines):
    pair = apply_with_class(cloudtrail_config, cloudtrail_lines[0])
    assert pair is not None
    ev, cls = pair
    assert cls in {"authentication", "api_activity"}


def test_apply_stream_skips_blank_lines(cloudtrail_config, cloudtrail_lines):
    lines = cloudtrail_lines + ["", "  "]
    out = list(apply_stream(cloudtrail_config, lines))
    assert len(out) == len(cloudtrail_lines)


def test_apply_stream_with_class_pairs(cloudtrail_config, cloudtrail_lines):
    out = list(apply_stream_with_class(cloudtrail_config, cloudtrail_lines))
    assert {cls for _, cls in out} <= {"authentication", "api_activity"}


def test_apply_returns_none_for_unparseable_regex_line():
    config = {
        "parser": {"regex": r"^X$", "groups": []},
        "classes": {"c": {"mapping": {"a": {"const": 1}}}},
    }
    assert apply(config, "no match") is None


def test_apply_with_class_returns_none_for_unparseable():
    config = {
        "parser": {"regex": r"^X$", "groups": []},
        "classes": {"c": {"mapping": {"a": {"const": 1}}}},
    }
    assert apply_with_class(config, "no match") is None
