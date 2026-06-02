"""Tests for the replay tool — re-applying a mapping over historical OCSF output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ocsf_mapper.replay import (
    iter_events,
    main as replay_main,
    render_summary,
    replay_path,
    replay_stream,
)


@pytest.fixture
def cloudtrail_jsonl_output(tmp_path, mappings_dir, samples_dir):
    """A small OCSF JSONL output to replay against. Uses the real cloudtrail
    mapping over 10 of the in-repo sample lines."""
    from ocsf_mapper.apply import apply_stream
    config = json.loads((mappings_dir / "cloudtrail.json").read_text())
    lines = (samples_dir / "cloudtrail.jsonl").read_text().splitlines()[:10]
    out = tmp_path / "history.jsonl"
    with out.open("w") as fp:
        for ev in apply_stream(config, lines):
            fp.write(json.dumps(ev) + "\n")
    return out


# ---------------------------------------------------------------------------
# iter_events
# ---------------------------------------------------------------------------


def test_iter_events_jsonl(cloudtrail_jsonl_output):
    events = list(iter_events(cloudtrail_jsonl_output))
    assert len(events) == 10
    assert all("class_uid" in e for e in events)
    assert all("raw_data" in e for e in events)


# ---------------------------------------------------------------------------
# replay_stream
# ---------------------------------------------------------------------------


def test_replay_stream_remaps_with_raw_data(cloudtrail_jsonl_output, mappings_dir):
    config = json.loads((mappings_dir / "cloudtrail.json").read_text())
    events = list(iter_events(cloudtrail_jsonl_output))
    out = list(replay_stream(events, config))
    assert len(out) == 10
    assert all(status == "ok" for _, status in out)
    # Round-trip should preserve class_uid since the mapping hasn't changed.
    for new_ev, _ in out:
        assert new_ev["class_uid"] in (3002, 6003)


def test_replay_stream_skips_events_without_raw_data(mappings_dir):
    config = json.loads((mappings_dir / "cloudtrail.json").read_text())
    events = [
        {"class_uid": 6003, "raw_data": "not actually a json line"},
        {"class_uid": 6003},  # no raw_data
        {"class_uid": 6003, "raw_data": ""},  # empty raw_data
    ]
    out = list(replay_stream(events, config))
    assert [s for _, s in out] == ["no_match", "no_raw", "no_raw"]


# ---------------------------------------------------------------------------
# replay_path — file-to-file
# ---------------------------------------------------------------------------


def test_replay_path_jsonl_to_jsonl(cloudtrail_jsonl_output, mappings_dir, tmp_path):
    out_path = tmp_path / "out.jsonl"
    result = replay_path(
        cloudtrail_jsonl_output,
        mappings_dir / "cloudtrail.json",
        out_path,
    )
    assert result["total"] == 10
    assert result["remapped"] == 10
    assert result["no_raw"] == 0
    assert result["no_match"] == 0
    # Output file should have one OCSF event per input event.
    assert len(out_path.read_text().splitlines()) == 10


def test_replay_path_handles_mixed_validity(cloudtrail_jsonl_output, mappings_dir, tmp_path):
    """Append a no-raw event to the input — replay should report it under no_raw."""
    text = cloudtrail_jsonl_output.read_text()
    text += json.dumps({"class_uid": 6003, "metadata": {"product": {"name": "x"}}}) + "\n"
    cloudtrail_jsonl_output.write_text(text)

    out_path = tmp_path / "out.jsonl"
    result = replay_path(
        cloudtrail_jsonl_output,
        mappings_dir / "cloudtrail.json",
        out_path,
    )
    assert result["total"] == 11
    assert result["no_raw"] == 1
    assert result["remapped"] == 10


# ---------------------------------------------------------------------------
# render_summary + main
# ---------------------------------------------------------------------------


def test_render_summary_format():
    s = render_summary({"total": 100, "remapped": 95, "no_raw": 3, "no_match": 2})
    assert "100" in s
    assert "95" in s
    assert "3" in s
    assert "2" in s


def test_main_missing_args_returns_nonzero(capsys):
    rc = replay_main(["only-one-arg"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower()


def test_main_zero_remapped_returns_nonzero(tmp_path, mappings_dir, capsys):
    """If nothing gets remapped, exit code 1 — useful as a CI gate."""
    empty = tmp_path / "empty.jsonl"
    empty.write_text(json.dumps({"class_uid": 1, "x": 1}) + "\n")  # no raw_data
    out = tmp_path / "out.jsonl"
    rc = replay_main([str(empty), str(mappings_dir / "cloudtrail.json"), str(out)])
    assert rc == 1


def test_main_happy_path(cloudtrail_jsonl_output, mappings_dir, tmp_path, capsys):
    out_path = tmp_path / "out.jsonl"
    rc = replay_main([
        str(cloudtrail_jsonl_output),
        str(mappings_dir / "cloudtrail.json"),
        str(out_path),
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "replayed 10" in err


# ---------------------------------------------------------------------------
# CLI integration via ocsf_mapper.cli main
# ---------------------------------------------------------------------------


def test_cli_replay_subcommand(cloudtrail_jsonl_output, mappings_dir, tmp_path, monkeypatch, capsys):
    from ocsf_mapper.cli import main as cli_main
    monkeypatch.chdir(mappings_dir.parent)
    out_path = tmp_path / "out.jsonl"
    rc = cli_main([
        "replay",
        str(cloudtrail_jsonl_output),
        str(mappings_dir / "cloudtrail.json"),
        str(out_path),
    ])
    assert rc == 0
    assert out_path.exists()
    assert len(out_path.read_text().splitlines()) == 10


# ---------------------------------------------------------------------------
# Parquet round-trip (skip if pyarrow not installed)
# ---------------------------------------------------------------------------


def test_replay_parquet_roundtrip(cloudtrail_jsonl_output, mappings_dir, tmp_path):
    pytest.importorskip("pyarrow")
    from ocsf_mapper.sinks.parquet import ParquetSink
    from ocsf_mapper.apply import apply_stream
    # Stage a parquet file with the same events
    config = json.loads((mappings_dir / "cloudtrail.json").read_text())
    parquet_in = tmp_path / "history.parquet"
    with ParquetSink(parquet_in) as sink:
        events = list(iter_events(cloudtrail_jsonl_output))
        sink.write_many(events)

    out_path = tmp_path / "replayed.parquet"
    result = replay_path(parquet_in, mappings_dir / "cloudtrail.json", out_path)
    assert result["remapped"] == 10
    assert out_path.exists()
