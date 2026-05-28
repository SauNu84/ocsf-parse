"""Tests for output sinks."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from ocsf_mapper.sinks import CsvSink, JsonlSink, StdoutSink, get_sink, infer_kind
from ocsf_mapper.sinks.csv import _flatten


SAMPLE = {
    "class_uid": 4002,
    "metadata": {"version": "1.9.0-dev", "product": {"name": "nginx"}},
    "src_endpoint": {"ip": "1.2.3.4"},
    "tags": ["a", "b"],
}


# ---------------------------------------------------------------------------
# JsonlSink
# ---------------------------------------------------------------------------


def test_jsonl_sink_writes_one_event_per_line(tmp_path):
    p = tmp_path / "out.jsonl"
    with JsonlSink(p) as s:
        n = s.write_many([SAMPLE, SAMPLE])
    assert n == 2
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == SAMPLE


def test_jsonl_sink_creates_parent_dirs(tmp_path):
    p = tmp_path / "nested" / "deep" / "out.jsonl"
    with JsonlSink(p) as s:
        s.write_one(SAMPLE)
    assert p.exists()


def test_jsonl_sink_close_idempotent(tmp_path):
    p = tmp_path / "out.jsonl"
    s = JsonlSink(p)
    s.write_one(SAMPLE)
    s.close()
    s.close()  # should not raise


# ---------------------------------------------------------------------------
# CsvSink + flatten
# ---------------------------------------------------------------------------


def test_flatten_dotted_keys():
    out = _flatten({"a": {"b": 1, "c": {"d": 2}}, "e": [1, 2]})
    assert out == {"a.b": 1, "a.c.d": 2, "e": "[1, 2]"}


def test_csv_sink_writes_header_and_rows(tmp_path):
    p = tmp_path / "out.csv"
    with CsvSink(p) as s:
        s.write_many([SAMPLE, SAMPLE])
    rows = list(csv.DictReader(p.open()))
    assert len(rows) == 2
    assert rows[0]["class_uid"] == "4002"
    assert rows[0]["metadata.product.name"] == "nginx"
    assert rows[0]["src_endpoint.ip"] == "1.2.3.4"


def test_csv_sink_union_of_columns_across_events(tmp_path):
    p = tmp_path / "out.csv"
    with CsvSink(p) as s:
        s.write_one({"a": 1})
        s.write_one({"a": 2, "b": 3})
    reader = csv.DictReader(p.open())
    header = reader.fieldnames
    assert set(header) == {"a", "b"}


# ---------------------------------------------------------------------------
# StdoutSink
# ---------------------------------------------------------------------------


def test_stdout_sink_writes_jsonl(capsys):
    with StdoutSink() as s:
        s.write_one(SAMPLE)
    out = capsys.readouterr().out
    assert json.loads(out.strip()) == SAMPLE


# ---------------------------------------------------------------------------
# get_sink / infer_kind
# ---------------------------------------------------------------------------


def test_infer_kind_from_extension():
    assert infer_kind("a.jsonl") == "jsonl"
    assert infer_kind("a.csv") == "csv"
    assert infer_kind("a.parquet") == "parquet"
    assert infer_kind("-") == "stdout"
    assert infer_kind(None) == "stdout"
    assert infer_kind("a.unknown") == "jsonl"


def test_get_sink_dispatches_concrete_types(tmp_path):
    assert isinstance(get_sink("stdout"), StdoutSink)
    assert isinstance(get_sink("jsonl", tmp_path / "x.jsonl"), JsonlSink)
    assert isinstance(get_sink("csv", tmp_path / "x.csv"), CsvSink)


def test_get_sink_unknown_raises():
    with pytest.raises(ValueError):
        get_sink("xml")


def test_get_sink_non_stdout_requires_path():
    with pytest.raises(ValueError):
        get_sink("jsonl", None)


# ---------------------------------------------------------------------------
# Parquet (optional dep)
# ---------------------------------------------------------------------------


def test_parquet_sink_roundtrip_when_available(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    from ocsf_mapper.sinks.parquet import ParquetSink

    p = tmp_path / "out.parquet"
    with ParquetSink(p) as s:
        s.write_many([SAMPLE, SAMPLE])
    table = pq.read_table(p)
    assert table.num_rows == 2
