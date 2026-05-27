"""Tests for the structural validator."""

from __future__ import annotations

from ocsf_mapper.validate import required_attrs, validate, validate_stream


def test_required_attrs_filters_to_required_only(schema):
    cls = schema.load_class("authentication")
    names = required_attrs(cls)
    # Every OCSF event class requires these:
    assert "class_uid" in names
    assert "metadata" in names
    # $include / $-prefixed keys are excluded:
    assert all(not n.startswith("$") for n in names)


def _minimal_authentication() -> dict:
    """An authentication event with every required attribute + the at_least_one constraint."""
    return {
        "metadata": {"version": "1.9.0-dev", "product": {"vendor_name": "x"}},
        "time": 1700000000000,
        "category_uid": 3,
        "class_uid": 3002,
        "activity_id": 1,
        "type_uid": 300201,
        "severity_id": 1,
        "user": {"name": "alice"},
        "service": {"name": "demo"},
    }


def test_validate_passes_for_minimal_valid_authentication(schema):
    assert validate(_minimal_authentication(), "authentication", schema=schema) == []


def test_validate_flags_at_least_one_constraint_violation(schema):
    ev = _minimal_authentication()
    # remove the constraint-satisfying field
    del ev["service"]
    errs = validate(ev, "authentication", schema=schema)
    assert any("at_least_one" in e for e in errs)


def test_validate_flags_missing_required(schema):
    errs = validate({}, "authentication", schema=schema)
    assert any("missing required attribute" in e for e in errs)


def test_validate_flags_activity_id_outside_enum(schema):
    ev = {**_minimal_authentication(), "activity_id": 42, "type_uid": 300242}
    errs = validate(ev, "authentication", schema=schema)
    assert any("activity_id=42" in e for e in errs)


def test_validate_allows_0_and_99_activity_sentinels(schema):
    for aid in (0, 99):
        ev = {**_minimal_authentication(), "activity_id": aid}
        errs = validate(ev, "authentication", schema=schema)
        assert not any("activity_id" in e for e in errs), errs


def test_validate_flags_unknown_category_uid(schema):
    ev = {**_minimal_authentication(), "category_uid": 999}
    errs = validate(ev, "authentication", schema=schema)
    assert any("category_uid=999" in e for e in errs)


def test_validate_stream_returns_only_failures(schema):
    good = _minimal_authentication()
    bad = {}
    out = validate_stream([good, bad, good], "authentication", schema=schema)
    assert [i for i, _ in out] == [1]
