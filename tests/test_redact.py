"""Tests for the PII redaction layer."""

from __future__ import annotations

import json

import pytest

from ocsf_mapper.redact import (
    ALL_KINDS,
    RedactingSink,
    redact_event,
    redact_text,
)
from ocsf_mapper.sinks import JsonlSink


# ---------------------------------------------------------------------------
# redact_text — per-kind
# ---------------------------------------------------------------------------


def test_redact_email():
    assert redact_text("alice@example.com") == "[REDACTED:email]"
    assert redact_text("bob+tag@sub.example.co.uk") == "[REDACTED:email]"
    # Doesn't match obvious non-emails:
    assert "REDACTED" not in redact_text("just a sentence with @ symbol")


def test_redact_ipv4_in_context():
    src = "from 10.0.1.42 to 203.0.113.50 (not 999.999.999.999)"
    out = redact_text(src)
    assert "[REDACTED:ipv4]" in out
    assert "10.0.1.42" not in out
    assert "203.0.113.50" not in out
    # Invalid IP (999) is left alone.
    assert "999.999.999.999" in out


def test_redact_ssn_with_and_without_dashes():
    assert redact_text("SSN 123-45-6789") == "SSN [REDACTED:ssn]"
    assert redact_text("SSN 123456789") == "SSN [REDACTED:ssn]"


def test_redact_phone_us():
    assert "[REDACTED:phone]" in redact_text("call +1 (555) 123-4567")
    assert "[REDACTED:phone]" in redact_text("555-123-4567")


def test_redact_jwt():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc-def_xyz"
    assert redact_text(jwt) == "[REDACTED:jwt]"


def test_redact_ccn_luhn_only():
    """Valid Luhn cards get redacted; the same digits +1 don't."""
    valid = "4111 1111 1111 1111"      # Luhn-OK demo card
    invalid = "4111 1111 1111 1112"     # not Luhn-OK
    assert redact_text(valid) == "[REDACTED:ccn]"
    assert redact_text(invalid) == invalid


# ---------------------------------------------------------------------------
# redact_text — subset selection + non-string passthrough
# ---------------------------------------------------------------------------


def test_redact_kinds_subset():
    src = "alice@example.com from 10.0.1.42"
    out = redact_text(src, kinds=["email"])
    assert "[REDACTED:email]" in out
    assert "10.0.1.42" in out  # not in kinds → not touched


def test_redact_text_passthrough_for_non_string():
    assert redact_text(42) == 42        # type: ignore[arg-type]
    assert redact_text(None) is None    # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# redact_event
# ---------------------------------------------------------------------------


def test_redact_event_walks_dicts_and_lists():
    ev = {
        "metadata": {"product": {"name": "nginx"}},
        "user": {"name": "alice@example.com"},
        "tags": ["10.0.1.42", "no-pii-here"],
        "count": 42,
    }
    out = redact_event(ev)
    assert out["user"]["name"] == "[REDACTED:email]"
    assert out["tags"][0] == "[REDACTED:ipv4]"
    assert out["tags"][1] == "no-pii-here"
    assert out["count"] == 42
    # Keys are never modified.
    assert "alice" not in str(list(out.keys()))


def test_redact_event_passthrough_scalar():
    assert redact_event(7) == 7
    assert redact_event("plain text") == "plain text"


# ---------------------------------------------------------------------------
# RedactingSink
# ---------------------------------------------------------------------------


def test_redacting_sink_pipes_into_wrapped(tmp_path):
    out = tmp_path / "out.jsonl"
    ev = {"user": {"name": "alice@example.com"}, "src": {"ip": "10.0.1.42"}}
    with RedactingSink(JsonlSink(out), kinds=ALL_KINDS) as s:
        s.write_many([ev, ev])
    lines = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    for ev_w in lines:
        assert ev_w["user"]["name"] == "[REDACTED:email]"
        assert ev_w["src"]["ip"] == "[REDACTED:ipv4]"


def test_redacting_sink_subset_kinds(tmp_path):
    out = tmp_path / "out.jsonl"
    ev = {"user": {"name": "alice@example.com"}, "src": {"ip": "10.0.1.42"}}
    with RedactingSink(JsonlSink(out), kinds=["email"]) as s:
        s.write_one(ev)
    e = json.loads(out.read_text().strip())
    assert e["user"]["name"] == "[REDACTED:email]"
    assert e["src"]["ip"] == "10.0.1.42"  # not redacted


def test_redacting_sink_close_idempotent(tmp_path):
    out = tmp_path / "out.jsonl"
    s = RedactingSink(JsonlSink(out))
    s.write_one({"x": "alice@x.com"})
    s.close()
    s.close()  # safe to double-close
