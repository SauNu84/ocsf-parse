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


# ---------------------------------------------------------------------------
# Security Lake-partitioned Parquet
# ---------------------------------------------------------------------------


def test_security_lake_sink_partitions_by_class_and_day(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    from ocsf_mapper.sinks.security_lake import SecurityLakeSink

    day1 = 1716818591000
    day2 = day1 + 24 * 3600 * 1000
    events = [
        {"class_uid": 3002, "time": day1, "user": {"name": "alice"}},
        {"class_uid": 3002, "time": day1, "user": {"name": "bob"}},
        {"class_uid": 6003, "time": day1, "api": {"operation": "GetObject"}},
        {"class_uid": 6003, "time": day2, "api": {"operation": "PutObject"}},
    ]
    with SecurityLakeSink(tmp_path) as s:
        s.write_many(events)
        partitions_before_close = dict(s.partitions())

    assert len(partitions_before_close) == 3
    files = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*.parquet"))
    assert len(files) == 3
    rel_strs = {str(f) for f in files}
    assert any("3002/eventDay=" in s for s in rel_strs)
    assert any("6003/eventDay=" in s for s in rel_strs)
    total = sum(pq.read_table(tmp_path / f).num_rows for f in files)
    assert total == 4


def test_security_lake_sink_handles_missing_time(tmp_path):
    pytest.importorskip("pyarrow")
    from ocsf_mapper.sinks.security_lake import SecurityLakeSink

    with SecurityLakeSink(tmp_path) as s:
        s.write_one({"class_uid": 4002})
    files = list(tmp_path.rglob("*.parquet"))
    assert len(files) == 1
    assert "eventDay=unknown" in str(files[0])


def test_get_sink_security_lake(tmp_path):
    pytest.importorskip("pyarrow")
    sink = get_sink("security-lake", tmp_path)
    assert sink.__class__.__name__ == "SecurityLakeSink"
    sink2 = get_sink("security_lake", tmp_path)
    assert sink2.__class__.__name__ == "SecurityLakeSink"


# ---------------------------------------------------------------------------
# Streaming / rolling SecurityLakeSink (Perf #1)
# ---------------------------------------------------------------------------


def test_security_lake_flushes_on_threshold(tmp_path):
    """With flush_every=N, each partition rolls a new file every N events."""
    pq = pytest.importorskip("pyarrow.parquet")
    from ocsf_mapper.sinks.security_lake import SecurityLakeSink

    day1 = 1779891791000  # 2026-05-27 14:23:11 UTC
    events = [{"class_uid": 6003, "time": day1, "x": i} for i in range(450)]

    with SecurityLakeSink(tmp_path, flush_every=200) as s:
        s.write_many(events)
        # After 450 inserts the bucket has 50 rows remaining (the tail).
        assert s.partitions() == {("6003", "20260527"): 50}

    files = sorted(tmp_path.rglob("*.parquet"))
    assert len(files) == 3
    rows = [pq.read_table(f).num_rows for f in files]
    assert rows == [200, 200, 50]


def test_security_lake_does_not_overwrite_existing_parts(tmp_path):
    """A second run on the same root should start naming from the next free part-N."""
    pq = pytest.importorskip("pyarrow.parquet")
    from ocsf_mapper.sinks.security_lake import SecurityLakeSink

    day1 = 1779891791000  # 2026-05-27
    # First run: 250 events at flush_every=200 → 2 parts.
    with SecurityLakeSink(tmp_path, flush_every=200) as s1:
        s1.write_many([{"class_uid": 6003, "time": day1, "x": i} for i in range(250)])
    assert len(list(tmp_path.rglob("*.parquet"))) == 2

    # Second run: more events on the same root.
    with SecurityLakeSink(tmp_path, flush_every=200) as s2:
        s2.write_many([{"class_uid": 6003, "time": day1, "y": i} for i in range(100)])
    parts = sorted(tmp_path.rglob("*.parquet"))
    # Should have 3 files now, numbered 00000, 00001 (from first run), 00002 (from second).
    assert [p.name for p in parts] == ["part-00000.parquet", "part-00001.parquet", "part-00002.parquet"]


def test_security_lake_flush_every_must_be_positive(tmp_path):
    from ocsf_mapper.sinks.security_lake import SecurityLakeSink
    with pytest.raises(ValueError):
        SecurityLakeSink(tmp_path, flush_every=0)


def test_security_lake_flushed_rows_tracks_disk_writes(tmp_path):
    pytest.importorskip("pyarrow")
    from ocsf_mapper.sinks.security_lake import SecurityLakeSink

    day1 = 1779891791000  # 2026-05-27
    with SecurityLakeSink(tmp_path, flush_every=10) as s:
        s.write_many([{"class_uid": 6003, "time": day1, "x": i} for i in range(25)])
        # 20 events flushed (2 × 10), 5 still buffered.
        assert s.flushed_rows() == {("6003", "20260527"): 20}
        assert s.partitions() == {("6003", "20260527"): 5}


# ---------------------------------------------------------------------------
# Parquet schema pre-declaration (Perf #6)
# ---------------------------------------------------------------------------


def test_parquet_sink_with_declared_schema(tmp_path):
    """A pre-declared schema is honoured on write (skips type re-inference)."""
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    from ocsf_mapper.sinks.parquet import ParquetSink
    from ocsf_mapper.sinks.security_lake import infer_schema_from

    sample = {"class_uid": 3002, "time": 1716818591000, "name": "alice"}
    schema = infer_schema_from(sample)
    out = tmp_path / "out.parquet"
    with ParquetSink(out, schema=schema) as s:
        s.write_many([sample, sample])
    table = pq.read_table(out)
    assert table.num_rows == 2
    assert table.schema.equals(schema)


def test_infer_schema_from_returns_pyarrow_schema():
    pa = pytest.importorskip("pyarrow")
    from ocsf_mapper.sinks.security_lake import infer_schema_from

    s = infer_schema_from({"a": 1, "b": "x"})
    assert "a" in s.names and "b" in s.names
