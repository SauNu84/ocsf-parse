"""Tests for the CEF and LEEF parser kinds in apply.parse_record."""

from __future__ import annotations

import pytest

from ocsf_mapper.apply import parse_record


# ---------------------------------------------------------------------------
# CEF
# ---------------------------------------------------------------------------


def test_cef_basic_parse():
    line = "CEF:0|Acme|FW|1.0|100|Blocked|7|src=1.2.3.4 dst=5.6.7.8 spt=80 act=deny"
    rec = parse_record(line, "cef")
    assert rec is not None
    assert rec["cef_version"] == "0"
    assert rec["device_vendor"] == "Acme"
    assert rec["device_product"] == "FW"
    assert rec["device_version"] == "1.0"
    assert rec["signature_id"] == "100"
    assert rec["name"] == "Blocked"
    assert rec["severity"] == "7"
    # Extension is parsed; keys exposed at top level too.
    assert rec["src"] == "1.2.3.4"
    assert rec["dst"] == "5.6.7.8"
    assert rec["spt"] == "80"
    assert rec["act"] == "deny"


def test_cef_extension_with_spaces_in_value():
    """Values run until the next ``<space>key=`` boundary."""
    line = ("CEF:0|Acme|IPS|1.0|EVT|Suspicious|5|"
            "src=10.0.0.1 msg=Possible exfiltration detected by host act=alert")
    rec = parse_record(line, "cef")
    assert rec["msg"] == "Possible exfiltration detected by host"
    assert rec["act"] == "alert"


def test_cef_escaped_pipe_in_header():
    """``\\|`` in a header field shouldn't terminate it."""
    line = "CEF:0|Acme \\| Corp|IPS|1.0|EVT|Detected|5|src=1.2.3.4"
    rec = parse_record(line, "cef")
    assert rec["device_vendor"] == "Acme | Corp"


def test_cef_escaped_equals_in_value():
    line = "CEF:0|Acme|IPS|1.0|EVT|Detected|5|src=1.2.3.4 query=a\\=b cs1=x"
    rec = parse_record(line, "cef")
    assert rec["query"] == "a=b"
    assert rec["cs1"] == "x"


def test_cef_missing_prefix_returns_none():
    assert parse_record("not a cef line", "cef") is None
    # A line that starts with "CEF:" but has too few pipes also fails.
    assert parse_record("CEF:0|Acme", "cef") is None


def test_cef_empty_extension_is_empty_dict():
    line = "CEF:0|Acme|FW|1.0|100|Blocked|7|"
    rec = parse_record(line, "cef")
    assert rec is not None
    assert rec["ext"] == {}


# ---------------------------------------------------------------------------
# LEEF
# ---------------------------------------------------------------------------


def test_leef_v1_tab_separated_extension():
    line = "LEEF:1.0|Acme|IPS|2.5|alert|src=10.0.0.1\tdst=192.0.2.1\tact=deny\tsev=8"
    rec = parse_record(line, "leef")
    assert rec is not None
    assert rec["leef_version"] == "1.0"
    assert rec["vendor"] == "Acme"
    assert rec["product"] == "IPS"
    assert rec["event_id"] == "alert"
    assert rec["src"] == "10.0.0.1"
    assert rec["dst"] == "192.0.2.1"
    assert rec["sev"] == "8"


def test_leef_v2_custom_delimiter():
    """LEEF 2.0 explicitly declares the extension delimiter."""
    line = "LEEF:2.0|Acme|IPS|2.5|alert|^|src=10.0.0.1^dst=192.0.2.1^act=deny^sev=8"
    rec = parse_record(line, "leef")
    assert rec["leef_version"] == "2.0"
    assert rec["src"] == "10.0.0.1"
    assert rec["dst"] == "192.0.2.1"
    assert rec["act"] == "deny"


def test_leef_v2_delim_aliases():
    """`\\t`, `x09`, `9`, `0x09` should all normalise to tab."""
    line = "LEEF:2.0|Acme|IPS|2.5|alert|\\t|src=1.1.1.1\tdst=2.2.2.2"
    rec = parse_record(line, "leef")
    assert rec["src"] == "1.1.1.1"
    assert rec["dst"] == "2.2.2.2"


def test_leef_missing_prefix_returns_none():
    assert parse_record("not a leef line", "leef") is None


def test_leef_too_few_pipes_returns_none():
    # Only "LEEF:1.0|Acme|IPS" — 3 fields, way short.
    assert parse_record("LEEF:1.0|Acme|IPS", "leef") is None


# ---------------------------------------------------------------------------
# integration: CEF + LEEF through apply()
# ---------------------------------------------------------------------------


def test_cef_through_apply_end_to_end():
    """A toy mapping that uses parser='cef' should produce an OCSF event."""
    from ocsf_mapper.apply import apply
    cfg = {
        "parser": "cef",
        "classes": {
            "detection_finding": {
                "mapping": {
                    "metadata.version":             {"const": "1.9.0-dev"},
                    "metadata.product.name":        {"path":  "$.device_product"},
                    "metadata.product.vendor_name": {"path":  "$.device_vendor"},
                    "category_uid":                 {"const": 2},
                    "class_uid":                    {"const": 2004},
                    "activity_id":                  {"const": 1},
                    "type_uid":                     {"expr":  "class_uid * 100 + activity_id"},
                    "severity_id":                  {"int":   "$.severity"},
                    "time":                         {"const": 1779891791000},
                    "finding_info.title":           {"path":  "$.name"},
                    "finding_info.uid":             {"path":  "$.signature_id"},
                    "src_endpoint.ip":              {"path":  "$.src"},
                    "dst_endpoint.ip":              {"path":  "$.dst"},
                }
            }
        },
    }
    line = "CEF:0|Acme|IPS|1.0|EVT-1|Blocked attack|5|src=1.2.3.4 dst=5.6.7.8 act=deny"
    ev = apply(cfg, line)
    assert ev is not None
    assert ev["src_endpoint"]["ip"] == "1.2.3.4"
    assert ev["dst_endpoint"]["ip"] == "5.6.7.8"
    assert ev["finding_info"]["title"] == "Blocked attack"
    assert ev["metadata"]["product"]["vendor_name"] == "Acme"
