"""Per-phase throughput measurement for a mapping × sample.

  ocsf-mapper benchmark mappings/cloudtrail.json samples/cloudtrail.jsonl

Times each phase of :func:`ocsf_mapper.apply.apply` (parse → route →
map → write) separately so you can see where the cycles go. Useful when
diagnosing a slow mapping, when sizing for 10 TB-class workloads, and
when verifying that a perf change actually moved the needle.

Repeats the sample if necessary to amortise startup cost — defaults to
running until at least 5 000 events have been processed or 2 s of wall
time has elapsed, whichever comes first.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

from ocsf_mapper.apply import map_record, parse_record, pick_class
from ocsf_mapper.sinks.jsonl import JsonlSink  # noqa: F401 — typing reference


def benchmark(
    config: dict,
    sample_path: Path | str,
    *,
    min_events: int = 5_000,
    max_seconds: float = 2.0,
) -> dict:
    """Run a timed measurement and return phase totals + summary stats."""
    lines = [
        ln for ln in Path(sample_path).read_text().splitlines() if ln.strip()
    ]
    if not lines:
        raise ValueError(f"sample is empty: {sample_path}")

    sample_size = Path(sample_path).stat().st_size
    parser = config["parser"]
    routing = config.get("routing")
    classes = config["classes"]
    # Pre-fetch the class block lookups so we don't include dict-traversal
    # overhead in the routing time.
    class_blocks = {name: blk for name, blk in classes.items()}

    # JsonlSink to in-memory bytes via a counter — we want to time the
    # serialisation cost, not disk I/O. Using a sink that just discards
    # would understate; using a real disk sink would overstate at scale.
    # Compromise: serialise to JSON bytes and accumulate length.
    from ocsf_mapper._fastjson import dumps as _json_dumps
    serialised_bytes = [0]

    def _write(event: dict) -> None:
        serialised_bytes[0] += len(_json_dumps(event)) + 1  # +1 for newline

    t_parse  = 0.0
    t_route  = 0.0
    t_map    = 0.0
    t_write  = 0.0
    t_start  = time.perf_counter()
    n_events = 0
    n_parsed = 0
    bytes_in = 0
    cursor = 0

    # Exit when EITHER threshold is hit (min_events collected OR max_seconds
    # of wall time elapsed). Using OR here would mean "stop when BOTH are
    # done" which lets a never-matching regex spin forever.
    while n_events < min_events and (time.perf_counter() - t_start) < max_seconds:
        line = lines[cursor % len(lines)]
        cursor += 1
        bytes_in += len(line) + 1  # +1 for the implicit newline

        # Bail out early if we've gone through far more lines than min_events
        # and still haven't emitted anything (regex misses every line, etc.).
        if cursor >= max(50_000, min_events * 100) and n_events == 0:
            break

        # ---- parse ----
        p0 = time.perf_counter()
        rec = parse_record(line, parser)
        p1 = time.perf_counter()
        t_parse += p1 - p0
        if rec is None:
            continue
        n_parsed += 1

        # ---- route ----
        r0 = time.perf_counter()
        cls = pick_class(rec, routing, class_blocks)
        r1 = time.perf_counter()
        t_route += r1 - r0

        # ---- map ----
        m0 = time.perf_counter()
        event = map_record(rec, class_blocks[cls])
        m1 = time.perf_counter()
        t_map += m1 - m0

        # ---- write ----
        w0 = time.perf_counter()
        _write(event)
        w1 = time.perf_counter()
        t_write += w1 - w0

        n_events += 1

    elapsed = time.perf_counter() - t_start
    total_phases = t_parse + t_route + t_map + t_write
    return {
        "sample_path":  str(sample_path),
        "sample_bytes": sample_size,
        "events_attempted": cursor,
        "events_parsed":    n_parsed,
        "events_emitted":   n_events,
        "elapsed_s":   elapsed,
        "events_per_s": n_events / elapsed if elapsed > 0 else 0.0,
        "bytes_per_s":  bytes_in / elapsed if elapsed > 0 else 0.0,
        "out_bytes":   serialised_bytes[0],
        "phases": {
            "parse": t_parse,
            "route": t_route,
            "map":   t_map,
            "write": t_write,
        },
        "phase_pct": {
            "parse": (t_parse / total_phases * 100) if total_phases else 0,
            "route": (t_route / total_phases * 100) if total_phases else 0,
            "map":   (t_map   / total_phases * 100) if total_phases else 0,
            "write": (t_write / total_phases * 100) if total_phases else 0,
        },
    }


def render_report(result: dict) -> str:
    """Pretty-print the benchmark result. Returns a string ready for stdout."""
    rate = result["events_per_s"]
    mbps = result["bytes_per_s"] / (1024 * 1024)
    lines = [
        f"mapping × {Path(result['sample_path']).name}",
        f"  {result['events_emitted']:>9,} events in {result['elapsed_s']:.3f}s",
        f"  {rate:>9,.0f} events/sec   {mbps:>6.1f} MB/sec",
        f"  {result['events_attempted']:>9,} lines attempted, "
        f"{result['events_parsed']:,} parsed, {result['events_emitted']:,} emitted",
        "",
        "  per-phase breakdown:",
    ]
    phases = result["phases"]
    pct = result["phase_pct"]
    for name in ("parse", "route", "map", "write"):
        bar_len = int(pct[name] / 2)
        bar = "█" * bar_len + " " * (50 - bar_len)
        lines.append(
            f"    {name:<6} {pct[name]:>5.1f}%  {phases[name] * 1000:>7.1f}ms  {bar}"
        )
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    """``python -m ocsf_mapper.benchmark <mapping> <sample>``."""
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        print("usage: benchmark <mapping> <sample> [--min-events N] [--max-seconds S]",
              file=sys.stderr)
        return 2
    mapping_path, sample_path = argv[0], argv[1]
    min_events = 5_000
    max_seconds = 2.0
    i = 2
    while i < len(argv):
        if argv[i] == "--min-events":
            min_events = int(argv[i + 1])
            i += 2
        elif argv[i] == "--max-seconds":
            max_seconds = float(argv[i + 1])
            i += 2
        else:
            print(f"unknown arg: {argv[i]}", file=sys.stderr)
            return 2
    config = json.loads(Path(mapping_path).read_text())
    result = benchmark(config, sample_path,
                        min_events=min_events, max_seconds=max_seconds)
    print(render_report(result), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
