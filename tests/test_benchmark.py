"""Tests for `ocsf-mapper benchmark`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ocsf_mapper.benchmark import benchmark, main as benchmark_main, render_report


@pytest.fixture
def nginx_config(mappings_dir):
    return json.loads((mappings_dir / "nginx.json").read_text())


def test_benchmark_returns_throughput_stats(nginx_config, samples_dir):
    result = benchmark(nginx_config, samples_dir / "nginx.log",
                        min_events=500, max_seconds=0.5)
    assert result["events_emitted"] >= 500
    assert result["elapsed_s"] > 0
    assert result["events_per_s"] > 0
    assert result["bytes_per_s"] > 0
    assert set(result["phases"]) == {"parse", "route", "map", "write"}
    # Each phase pct is between 0 and 100 and they roughly sum to 100.
    s = sum(result["phase_pct"].values())
    assert 99 < s < 101


def test_benchmark_phases_are_all_positive(nginx_config, samples_dir):
    """Each phase contributes some nonzero time on a real mapping × sample."""
    result = benchmark(nginx_config, samples_dir / "nginx.log",
                        min_events=200, max_seconds=0.3)
    for phase, t in result["phases"].items():
        assert t > 0, f"{phase} reported zero time — suspect a timing bug"


def test_benchmark_handles_regex_misses(samples_dir, tmp_path):
    """A mapping whose regex never matches still completes; just emits no events."""
    cfg = {
        "parser": {"regex": "^IMPOSSIBLE_PATTERN_XYZ$", "groups": []},
        "classes": {"c": {"mapping": {"class_uid": {"const": 1}}}},
    }
    # Use any non-empty sample
    result = benchmark(cfg, samples_dir / "nginx.log",
                        min_events=5, max_seconds=0.1)
    assert result["events_parsed"] == 0
    assert result["events_emitted"] == 0


def test_benchmark_raises_on_empty_sample(nginx_config, tmp_path):
    empty = tmp_path / "empty.log"
    empty.write_text("")
    with pytest.raises(ValueError):
        benchmark(nginx_config, empty)


def test_render_report_contains_phase_breakdown(nginx_config, samples_dir):
    result = benchmark(nginx_config, samples_dir / "nginx.log",
                        min_events=100, max_seconds=0.3)
    text = render_report(result)
    assert "per-phase breakdown" in text
    for phase in ("parse", "route", "map", "write"):
        assert phase in text
    assert "events/sec" in text and "MB/sec" in text


def test_benchmark_main_runs_and_exits_zero(mappings_dir, samples_dir, capsys):
    rc = benchmark_main([
        str(mappings_dir / "nginx.json"),
        str(samples_dir / "nginx.log"),
        "--min-events", "200",
        "--max-seconds", "0.3",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "events/sec" in out


def test_benchmark_main_missing_args_returns_nonzero(capsys):
    rc = benchmark_main(["only-one-arg"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower()


def test_benchmark_main_rejects_unknown_arg(mappings_dir, samples_dir, capsys):
    rc = benchmark_main([
        str(mappings_dir / "nginx.json"),
        str(samples_dir / "nginx.log"),
        "--bogus", "x",
    ])
    assert rc == 2
    assert "unknown arg" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# CLI subcommand integration
# ---------------------------------------------------------------------------


def test_cli_benchmark_subcommand_runs(mappings_dir, samples_dir, monkeypatch, capsys):
    from ocsf_mapper.cli import main as cli_main
    monkeypatch.chdir(mappings_dir.parent)
    rc = cli_main([
        "benchmark",
        str(mappings_dir / "nginx.json"),
        str(samples_dir / "nginx.log"),
        "--min-events", "200",
        "--max-seconds", "0.3",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "events/sec" in out
    assert "parse" in out and "map" in out
