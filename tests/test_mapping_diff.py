"""Tests for `ocsf-mapper diff`."""

from __future__ import annotations

import json

import pytest

from ocsf_mapper.mapping_diff import (
    diff_mappings,
    main as diff_main,
    render_text_report,
    _has_any_change,
    _op_kind,
)


# ---------------------------------------------------------------------------
# _op_kind
# ---------------------------------------------------------------------------


def test_op_kind_recognises_all_dsl_ops():
    for kind in ("const", "path", "group", "raw", "lookup", "time",
                  "range", "int", "bool", "expr", "for_each"):
        assert _op_kind({kind: "x"}) == kind


def test_op_kind_unknown():
    assert _op_kind({"chimera": True}) == "unknown"


def test_op_kind_literal():
    assert _op_kind(42) == "literal"
    assert _op_kind("plain") == "literal"


# ---------------------------------------------------------------------------
# diff_mappings — header
# ---------------------------------------------------------------------------


def test_diff_picks_up_metadata_changes():
    a = {"source_name": "foo", "vendor": "Acme", "parser": "json", "classes": {}}
    b = {"source_name": "foo", "vendor": "AcmeCloud", "parser": "json", "classes": {}}
    d = diff_mappings(a, b)
    assert "vendor" in d["header"]
    assert d["header"]["vendor"] == {"a": "Acme", "b": "AcmeCloud"}


def test_diff_parser_marks_same_or_different():
    same = diff_mappings(
        {"parser": "json", "classes": {}},
        {"parser": "json", "classes": {}},
    )
    diff = diff_mappings(
        {"parser": "json", "classes": {}},
        {"parser": {"regex": "x", "groups": []}, "classes": {}},
    )
    assert same["header"]["parser"] == "same"
    assert diff["header"]["parser"] == "different"


# ---------------------------------------------------------------------------
# diff_mappings — routing
# ---------------------------------------------------------------------------


def test_diff_detects_routing_field_change():
    a = {"parser": "json", "routing": {"field": "$.x", "rules": []}, "classes": {}}
    b = {"parser": "json", "routing": {"field": "$.y", "rules": []}, "classes": {}}
    d = diff_mappings(a, b)
    assert d["routing"]["field_changed"] is True
    assert d["routing"]["a_field"] == "$.x"
    assert d["routing"]["b_field"] == "$.y"


def test_diff_detects_routing_rule_count_change():
    a = {"parser": "json", "routing": {"field": "$.x", "rules": [{"default": True, "class": "c"}]},
         "classes": {}}
    b = {"parser": "json",
         "routing": {"field": "$.x", "rules": [
             {"matches": ["a"], "class": "c"},
             {"default": True, "class": "c"},
         ]},
         "classes": {}}
    d = diff_mappings(a, b)
    assert d["routing"]["rules_changed"] is True
    assert d["routing"]["n_rules_a"] == 1
    assert d["routing"]["n_rules_b"] == 2


# ---------------------------------------------------------------------------
# diff_mappings — classes
# ---------------------------------------------------------------------------


def test_diff_class_set_difference():
    a = {"parser": "json", "classes": {"only_a": {"mapping": {}}, "shared": {"mapping": {}}}}
    b = {"parser": "json", "classes": {"only_b": {"mapping": {}}, "shared": {"mapping": {}}}}
    d = diff_mappings(a, b)
    assert d["classes"]["a_only"] == ["only_a"]
    assert d["classes"]["b_only"] == ["only_b"]
    assert "shared" in d["classes"]["shared"]


def test_diff_target_added_and_removed():
    a = {"parser": "json", "classes": {"c": {"mapping": {
        "k1": {"const": 1},
        "k2": {"const": 2},
    }}}}
    b = {"parser": "json", "classes": {"c": {"mapping": {
        "k1": {"const": 1},
        "k3": {"const": 3},
    }}}}
    d = diff_mappings(a, b)
    block = d["classes"]["shared"]["c"]
    assert block["added"] == ["k3"]
    assert block["removed"] == ["k2"]
    assert block["common"] == ["k1"]
    assert block["op_changed"] == []


def test_diff_op_kind_change_flagged():
    a = {"parser": "json", "classes": {"c": {"mapping": {"k": {"const": 1}}}}}
    b = {"parser": "json", "classes": {"c": {"mapping": {"k": {"path": "$.x"}}}}}
    d = diff_mappings(a, b)
    ch = d["classes"]["shared"]["c"]["op_changed"]
    assert len(ch) == 1
    assert ch[0]["a_kind"] == "const" and ch[0]["b_kind"] == "path"


def test_diff_op_body_change_same_kind():
    a = {"parser": "json", "classes": {"c": {"mapping": {"k": {"const": "v1"}}}}}
    b = {"parser": "json", "classes": {"c": {"mapping": {"k": {"const": "v2"}}}}}
    d = diff_mappings(a, b)
    ch = d["classes"]["shared"]["c"]["op_changed"]
    assert len(ch) == 1
    assert ch[0]["a_kind"] == ch[0]["b_kind"] == "const"
    assert ch[0]["a_detail"] != ch[0]["b_detail"]


# ---------------------------------------------------------------------------
# render_text_report
# ---------------------------------------------------------------------------


def test_render_text_report_for_real_mappings(mappings_dir):
    """nginx vs apache — both http_activity, sharing structure."""
    a = json.loads((mappings_dir / "nginx.json").read_text())
    b = json.loads((mappings_dir / "apache.json").read_text())
    text = render_text_report(diff_mappings(a, b), names=("nginx", "apache"))
    assert "# Mapping diff: nginx → apache" in text
    # Vendor differs in the metadata block.
    assert "vendor:" in text


def test_render_identical_mappings_says_no_differences():
    cfg = {"parser": "json", "classes": {"c": {"mapping": {"k": {"const": 1}}}}}
    text = render_text_report(diff_mappings(cfg, cfg))
    assert "no differences detected" in text


def test_has_any_change_detects_class_only_in_one():
    a = {"parser": "json", "classes": {"only_a": {"mapping": {}}}}
    b = {"parser": "json", "classes": {}}
    assert _has_any_change(diff_mappings(a, b))


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------


def test_main_text_output(mappings_dir, capsys):
    rc = diff_main([
        str(mappings_dir / "nginx.json"),
        str(mappings_dir / "apache.json"),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Mapping diff: nginx → apache" in out


def test_main_json_output(mappings_dir, capsys):
    rc = diff_main([
        str(mappings_dir / "nginx.json"),
        str(mappings_dir / "apache.json"),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "header" in payload
    assert "routing" in payload
    assert "classes" in payload


def test_main_missing_args(capsys):
    rc = diff_main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower()


def test_cli_diff_subcommand(mappings_dir, monkeypatch, capsys):
    from ocsf_mapper.cli import main as cli_main
    monkeypatch.chdir(mappings_dir.parent)
    rc = cli_main([
        "diff",
        str(mappings_dir / "okta.json"),
        str(mappings_dir / "azure_ad_signin.json"),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    # Two authentication mappings — share the class but with different ops.
    assert "Mapping diff:" in out
