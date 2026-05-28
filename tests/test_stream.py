"""Tests for stream/tail mode."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from ocsf_mapper.sinks import JsonlSink
from ocsf_mapper.stream import stream_apply, tail_file


def _delayed_appender(path: Path, lines: list[str], delays: list[float], stop: threading.Event):
    """Append lines to ``path`` with per-line delays, then signal stop."""
    for ln, dt in zip(lines, delays):
        time.sleep(dt)
        with path.open("a") as f:
            f.write(ln)
    time.sleep(0.15)
    stop.set()


def test_tail_file_emits_only_new_lines_by_default(tmp_path):
    p = tmp_path / "live.log"
    p.write_text("preexisting line 1\npreexisting line 2\n")

    stop = threading.Event()
    threading.Thread(
        target=_delayed_appender,
        args=(p, ["new A\n", "new B\n"], [0.1, 0.1], stop),
        daemon=True,
    ).start()

    out = list(tail_file(p, poll_interval=0.02, stop=stop))
    assert out == ["new A\n", "new B\n"]


def test_tail_file_from_start_emits_existing_lines(tmp_path):
    p = tmp_path / "live.log"
    p.write_text("alpha\nbeta\ngamma\n")
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    out = list(tail_file(p, poll_interval=0.02, from_start=True, stop=stop))
    assert out == ["alpha\n", "beta\n", "gamma\n"]


def test_tail_file_buffers_partial_lines(tmp_path):
    """A line written without a trailing newline shouldn't be yielded until
    the newline arrives. Set up the partial deterministically (before
    tail_file starts) to keep the test stable on slower test runners."""
    p = tmp_path / "live.log"
    p.write_text("partial...")   # partial line present from the start
    stop = threading.Event()

    def writer():
        time.sleep(0.15)         # let tail_file see EOF + buffer the partial
        with p.open("a") as f:
            f.write(" rest\n")    # now complete the line
        time.sleep(0.15)
        stop.set()

    threading.Thread(target=writer, daemon=True).start()
    out = list(tail_file(p, poll_interval=0.02, from_start=True, stop=stop))
    assert out == ["partial... rest\n"]


def test_stream_apply_pipes_into_sink(tmp_path):
    sample = '{"event_type":"login","ts":"2026-05-27T14:23:11Z","user":"alice"}\n'
    config = {
        "parser": "json",
        "classes": {
            "demo": {
                "mapping": {
                    "metadata.version": {"const": "1.9.0-dev"},
                    "metadata.product.name": {"const": "demo"},
                    "category_uid": {"const": 3},
                    "class_uid": {"const": 3002},
                    "class_name": {"const": "Authentication"},
                    "activity_id": {"const": 1},
                    "severity_id": {"const": 1},
                    "type_uid": {"expr": "class_uid * 100 + activity_id"},
                    "time": {"time": "$.ts", "format": "iso8601"},
                    "user.name": {"path": "$.user"},
                    "service.name": {"const": "demo"},
                }
            }
        },
    }

    live = tmp_path / "live.jsonl"
    live.write_text("")
    out = tmp_path / "out.jsonl"

    stop = threading.Event()
    threading.Thread(
        target=_delayed_appender,
        args=(live, [sample, sample], [0.1, 0.05], stop),
        daemon=True,
    ).start()

    with JsonlSink(out) as sink:
        n = stream_apply(config, live, sink, poll_interval=0.02, stop=stop)

    assert n == 2
    written = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(written) == 2
    assert all(e["class_uid"] == 3002 for e in written)
