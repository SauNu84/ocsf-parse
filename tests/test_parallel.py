"""Tests for multiprocess apply."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ocsf_mapper.parallel import (
    _line_aligned_ranges,
    _worker_sink_target,
    apply_parallel,
)


# ---------------------------------------------------------------------------
# Byte-range computation
# ---------------------------------------------------------------------------


def test_line_aligned_ranges_partition_a_file(tmp_path):
    p = tmp_path / "log"
    # 10 lines of varying width
    p.write_text("".join(f"line-{i:02d}-data\n" for i in range(10)))
    size = p.stat().st_size

    ranges = _line_aligned_ranges(p, 4)
    # Ranges should be contiguous and cover the whole file.
    assert ranges[0][0] == 0
    assert ranges[-1][1] == size
    for (_, end), (next_start, _) in zip(ranges, ranges[1:]):
        assert end == next_start

    # Each boundary should land at a newline (start of a fresh line).
    contents = p.read_bytes()
    for start, _ in ranges[1:]:
        assert start == 0 or contents[start - 1:start] == b"\n", (
            f"boundary at {start} is not a line start"
        )


def test_line_aligned_ranges_empty_file(tmp_path):
    p = tmp_path / "empty"
    p.write_text("")
    assert _line_aligned_ranges(p, 4) == [(0, 0)]


def test_line_aligned_ranges_single_worker(tmp_path):
    p = tmp_path / "log"
    p.write_text("a\nb\nc\n")
    assert _line_aligned_ranges(p, 1) == [(0, 6)]


# ---------------------------------------------------------------------------
# _worker_sink_target
# ---------------------------------------------------------------------------


def test_worker_sink_target_suffixes_file_sinks(tmp_path):
    out = tmp_path / "events.jsonl"
    p, kwargs = _worker_sink_target(out, "jsonl", worker_id=3, total_workers=4)
    assert p == tmp_path / "events.03.jsonl"
    assert kwargs == {}


def test_worker_sink_target_security_lake_uses_prefix(tmp_path):
    p, kwargs = _worker_sink_target(tmp_path, "security-lake", worker_id=7, total_workers=8)
    assert p == tmp_path
    assert kwargs == {"file_prefix": "part-w07"}


def test_worker_sink_target_single_worker_no_suffix(tmp_path):
    out = tmp_path / "events.jsonl"
    p, kwargs = _worker_sink_target(out, "jsonl", worker_id=0, total_workers=1)
    assert p == out
    assert kwargs == {}


def test_worker_sink_target_stdout_needs_no_path():
    p, kwargs = _worker_sink_target(None, "stdout", worker_id=0)
    assert p is None
    assert kwargs == {}


def test_worker_sink_target_missing_path_for_file_sink_raises():
    with pytest.raises(ValueError):
        _worker_sink_target(None, "jsonl", worker_id=0, total_workers=2)


# ---------------------------------------------------------------------------
# apply_parallel end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture
def cloudtrail_config(mappings_dir):
    return json.loads((mappings_dir / "cloudtrail.json").read_text())


def test_apply_parallel_jsonl_writes_per_worker_files(tmp_path, cloudtrail_config, samples_dir):
    out = tmp_path / "events.jsonl"
    n = apply_parallel(
        cloudtrail_config, samples_dir / "cloudtrail.jsonl", out,
        n_workers=4, sink_kind="jsonl",
    )
    parts = sorted(tmp_path.glob("events.*.jsonl"))
    assert len(parts) == 4
    total = sum(len(p.read_text().splitlines()) for p in parts)
    assert total == n == 100


def test_apply_parallel_matches_sequential_event_set(tmp_path, cloudtrail_config, samples_dir):
    """Multi-worker output should contain the same events as single-process —
    order across workers is unspecified, so compare as sets of class_uids."""
    from ocsf_mapper import apply_stream
    seq_events = list(apply_stream(
        cloudtrail_config,
        (samples_dir / "cloudtrail.jsonl").read_text().splitlines(),
    ))

    out = tmp_path / "events.jsonl"
    n = apply_parallel(
        cloudtrail_config, samples_dir / "cloudtrail.jsonl", out,
        n_workers=4, sink_kind="jsonl",
    )
    parts = sorted(tmp_path.glob("events.*.jsonl"))
    par_events = []
    for p in parts:
        for line in p.read_text().splitlines():
            par_events.append(json.loads(line))

    assert n == len(par_events) == len(seq_events)
    # Same multiset of (class_uid, class_name) pairs.
    assert sorted((e["class_uid"], e["class_name"]) for e in par_events) == \
           sorted((e["class_uid"], e["class_name"]) for e in seq_events)


def test_apply_parallel_security_lake_uses_per_worker_prefix(tmp_path, cloudtrail_config, samples_dir):
    pytest.importorskip("pyarrow")
    n = apply_parallel(
        cloudtrail_config, samples_dir / "cloudtrail.jsonl", tmp_path,
        n_workers=3, sink_kind="security-lake",
    )
    # Worker prefixes should appear in the part filenames.
    parts = sorted(tmp_path.rglob("*.parquet"))
    prefixes = {p.name.rsplit("-", 1)[0] for p in parts}  # e.g. {part-w00, part-w01, ...}
    assert all(pre.startswith("part-w") for pre in prefixes)
    assert len(prefixes) >= 1  # at least one worker had events
    assert n == 100


def test_apply_parallel_single_worker_short_circuits(tmp_path, cloudtrail_config, samples_dir):
    out = tmp_path / "events.jsonl"
    n = apply_parallel(
        cloudtrail_config, samples_dir / "cloudtrail.jsonl", out,
        n_workers=1, sink_kind="jsonl",
    )
    # n_workers=1 → no .00. suffix, just the single output path.
    assert out.exists()
    assert len(out.read_text().splitlines()) == n == 100


def test_apply_parallel_missing_input_raises(tmp_path, cloudtrail_config):
    with pytest.raises(FileNotFoundError):
        apply_parallel(
            cloudtrail_config, tmp_path / "nope.jsonl", tmp_path / "out.jsonl",
            n_workers=2,
        )


def test_apply_parallel_skips_blank_lines(tmp_path, cloudtrail_config, samples_dir):
    """Blank/whitespace lines in the input shouldn't produce events or crash."""
    src = (samples_dir / "cloudtrail.jsonl").read_text()
    padded = tmp_path / "padded.jsonl"
    # Inject blank lines between every real line.
    padded.write_text("\n\n  \n".join(src.splitlines()) + "\n")
    out = tmp_path / "out.jsonl"
    n = apply_parallel(cloudtrail_config, padded, out, n_workers=2, sink_kind="jsonl")
    assert n == 100  # same as the non-padded version


def test_apply_parallel_stdout_forces_single_process(tmp_path, cloudtrail_config, samples_dir, capsys):
    """stdout sink can't be safely interleaved across workers — must serialize."""
    # Won't actually open stdout in tests (would dump JSON to test output),
    # but we can confirm it doesn't crash and writes events.
    n = apply_parallel(
        cloudtrail_config, samples_dir / "cloudtrail.jsonl", None,
        n_workers=4, sink_kind="stdout",
    )
    capsys.readouterr()  # discard stdout
    assert n == 100
