"""Unit tests for every op kind in :mod:`ocsf_mapper.ops`."""

from __future__ import annotations

import pytest

from ocsf_mapper.ops import apply_op, get_path, resolve_expr, set_path


# ---------------------------------------------------------------------------
# get_path
# ---------------------------------------------------------------------------


def test_get_path_dotted_dict():
    assert get_path({"a": {"b": {"c": 1}}}, "$.a.b.c") == 1


def test_get_path_list_index():
    assert get_path({"items": [{"id": "x"}, {"id": "y"}]}, "$.items.1.id") == "y"


def test_get_path_returns_none_for_missing_keys():
    assert get_path({"a": 1}, "$.b.c") is None


def test_get_path_returns_none_for_out_of_range_list():
    assert get_path({"items": [1]}, "$.items.5") is None


def test_get_path_handles_none_path():
    assert get_path({"a": 1}, None) is None


def test_get_path_short_circuits_on_none():
    # Walking through a None mid-path returns None without raising.
    assert get_path({"a": None}, "$.a.b") is None


def test_get_path_traverses_list_with_non_index_part():
    # Hitting a list with a non-integer key segment returns None (not an error).
    assert get_path({"a": [1, 2]}, "$.a.foo") is None


# ---------------------------------------------------------------------------
# set_path
# ---------------------------------------------------------------------------


def test_set_path_creates_nested_dicts():
    d: dict = {}
    set_path(d, "a.b.c", 7)
    assert d == {"a": {"b": {"c": 7}}}


def test_set_path_skips_none_values():
    d: dict = {}
    set_path(d, "a.b", None)
    assert d == {}


# ---------------------------------------------------------------------------
# resolve_expr
# ---------------------------------------------------------------------------


def test_resolve_expr_treats_dollar_string_as_path():
    assert resolve_expr("$.x", {"x": 42}) == 42


def test_resolve_expr_returns_literal_for_non_dollar_string():
    assert resolve_expr("hello", {}) == "hello"


def test_resolve_expr_returns_literal_for_non_string():
    assert resolve_expr(7, {}) == 7


# ---------------------------------------------------------------------------
# apply_op — one test per op kind
# ---------------------------------------------------------------------------


def test_op_const():
    assert apply_op({"const": "x"}, {}) == "x"


def test_op_path():
    assert apply_op({"path": "$.a"}, {"a": 1}) == 1


def test_op_group():
    rec = {"__groups__": {"name": "alice"}}
    assert apply_op({"group": "name"}, rec) == "alice"


def test_op_group_missing_returns_none():
    assert apply_op({"group": "name"}, {}) is None


def test_op_raw():
    assert apply_op({"raw": True}, {"__raw__": "hello"}) == "hello"


def test_op_lookup_hit():
    op = {"lookup": "$.k", "table": {"a": 1}, "default": 99}
    assert apply_op(op, {"k": "a"}) == 1


def test_op_lookup_miss_returns_default():
    op = {"lookup": "$.k", "table": {"a": 1}, "default": 99}
    assert apply_op(op, {"k": "z"}) == 99


def test_op_lookup_null_with_if_null():
    op = {"lookup": "$.k", "table": {}, "if_null": "missing", "default": "x"}
    assert apply_op(op, {}) == "missing"


def test_op_lookup_null_falls_back_to_default_when_no_if_null():
    op = {"lookup": "$.k", "table": {}, "default": "x"}
    assert apply_op(op, {}) == "x"


def test_op_lookup_prefix_match_hit():
    op = {
        "lookup": "$.k",
        "table": {"Create": 1, "Delete": 4},
        "prefix_match": True,
        "default": 99,
    }
    assert apply_op(op, {"k": "CreateBucket"}) == 1


def test_op_lookup_prefix_match_miss_returns_default():
    op = {
        "lookup": "$.k",
        "table": {"Create": 1},
        "prefix_match": True,
        "default": 99,
    }
    assert apply_op(op, {"k": "Inspect"}) == 99


def test_op_time_iso8601_zulu():
    op = {"time": "$.t", "format": "iso8601"}
    # 2024-01-01T00:00:00Z = 1704067200000 ms
    assert apply_op(op, {"t": "2024-01-01T00:00:00Z"}) == 1704067200000


def test_op_time_epoch_ms_passthrough():
    op = {"time": "$.t", "format": "epoch_ms"}
    assert apply_op(op, {"t": "1700000000000"}) == 1700000000000


def test_op_time_strptime():
    op = {"time": "$.t", "format": "strptime:%Y/%m/%d %H:%M:%S"}
    # interpreted as naive local — we just check it's an int (epoch ms)
    out = apply_op(op, {"t": "2024/01/01 00:00:00"})
    assert isinstance(out, int)


def test_op_time_unknown_format_raises():
    op = {"time": "$.t", "format": "ufo"}
    with pytest.raises(ValueError):
        apply_op(op, {"t": "2024-01-01T00:00:00Z"})


def test_op_time_null_input_returns_none():
    op = {"time": "$.t", "format": "iso8601"}
    assert apply_op(op, {}) is None


def test_op_range_in_bucket():
    op = {"range": "$.code", "ranges": [[200, 299, 1], [400, 499, 2]], "default": 0}
    assert apply_op(op, {"code": 201}) == 1


def test_op_range_default():
    op = {"range": "$.code", "ranges": [[200, 299, 1]], "default": 0}
    assert apply_op(op, {"code": 500}) == 0


def test_op_range_null_input_returns_default():
    op = {"range": "$.code", "ranges": [[1, 9, "x"]], "default": "fallback"}
    assert apply_op(op, {}) == "fallback"


def test_op_int():
    assert apply_op({"int": "$.x"}, {"x": "42"}) == 42


def test_op_int_null_returns_none():
    assert apply_op({"int": "$.x"}, {}) is None


def test_op_bool_truthy():
    assert apply_op({"bool": "$.x"}, {"x": "yes"}) is True


def test_op_bool_falsy():
    assert apply_op({"bool": "$.x"}, {"x": "no"}) is False


def test_op_bool_null_returns_none():
    assert apply_op({"bool": "$.x"}, {}) is None


def test_op_expr_arithmetic():
    out = apply_op(
        {"expr": "class_uid * 100 + activity_id"},
        {},
        {"class_uid": 3002, "activity_id": 1},
    )
    assert out == 300201


def test_op_expr_unknown_var_returns_none():
    # Eval failure (NameError) is swallowed and returns None.
    assert apply_op({"expr": "missing_var + 1"}, {}, {}) is None


def test_op_unknown_raises():
    with pytest.raises(ValueError):
        apply_op({"chimera": True}, {})
