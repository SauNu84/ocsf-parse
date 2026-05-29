"""Multiprocess ``apply`` for inputs that don't fit one CPU.

Splits a file into ``n_workers`` line-aligned byte ranges and runs
:func:`ocsf_mapper.apply.apply` in a separate process per range. The
boundary computation pre-aligns to newlines so each worker reads a
contiguous slice of whole lines — no over- or under-reads.

Output layout per sink kind:

* ``jsonl`` / ``csv`` / ``parquet``: each worker writes
  ``<output>.<NN>.<ext>``. Concatenating the parts gives the same content
  the single-process path would have produced (modulo event ordering —
  apply_parallel makes no ordering guarantees across workers).
* ``security-lake`` / ``security_lake``: every worker writes to the same
  ``<root>`` but with a worker-distinct ``file_prefix`` (``part-wNN``)
  so they don't collide on part numbers. Result is the same partitioned
  tree, just with more files.
* ``stdout``: forced to single-process (no safe way to interleave
  multiple writers on the same fd).

Use from the SDK or via ``ocsf-mapper apply ... --workers N``.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional


def _line_aligned_ranges(path: Path, n_workers: int) -> list[tuple[int, int]]:
    """Return ``[(start, end)]`` byte ranges that each start *and* end at line boundaries.

    Each worker's range covers full lines. Concatenating all ranges
    losslessly reconstructs the file (assuming a trailing newline; tail
    bytes without a newline are still included).
    """
    size = path.stat().st_size
    if size == 0 or n_workers <= 1:
        return [(0, size)]

    chunk = max(1, size // n_workers)
    ranges: list[tuple[int, int]] = []
    boundary = 0
    with path.open("rb") as f:
        for i in range(n_workers - 1):
            target = (i + 1) * chunk
            if target <= boundary:
                continue  # the previous boundary already passed this target
            f.seek(target)
            f.readline()  # consume to end-of-line
            new_boundary = f.tell()
            if new_boundary > boundary:
                ranges.append((boundary, new_boundary))
                boundary = new_boundary
    if boundary < size:
        ranges.append((boundary, size))
    return ranges


def _worker_sink_target(
    output_path: Optional[Path],
    sink_kind: str,
    worker_id: int,
    total_workers: int = 1,
) -> tuple[Optional[Path], dict]:
    """Compute the per-worker output path + any sink kwargs that need a worker stamp."""
    if sink_kind == "stdout":
        return None, {}
    if output_path is None:
        raise ValueError(f"sink kind {sink_kind!r} requires an output path")
    if total_workers <= 1:
        # Only one writer — no need to disambiguate per worker.
        return output_path, {}
    if sink_kind in ("security-lake", "security_lake"):
        # Same root, but prefix-distinct so workers don't collide on part numbers.
        return output_path, {"file_prefix": f"part-w{worker_id:02d}"}
    # File-style sinks: foo.jsonl → foo.00.jsonl, foo.01.jsonl, ...
    suffix = output_path.suffix
    stem = output_path.with_suffix("")
    return Path(f"{stem}.{worker_id:02d}{suffix}"), {}


def _run_worker(
    config: dict,
    input_path: str,
    start: int,
    end: int,
    worker_id: int,
    output_path_str: Optional[str],
    sink_kind: str,
    extra_sink_kwargs: dict,
    total_workers: int = 1,
) -> int:
    """Process-pool target: read [start, end) of the input, apply, write to own sink."""
    # All imports happen inside the worker so the executor doesn't have to
    # pickle the apply/get_sink callables — only the args do.
    from ocsf_mapper.apply import apply as _apply
    from ocsf_mapper.sinks import get_sink

    sink_path, kwargs = _worker_sink_target(
        Path(output_path_str) if output_path_str else None,
        sink_kind,
        worker_id,
        total_workers=total_workers,
    )
    kwargs.update(extra_sink_kwargs)
    sink = get_sink(sink_kind, sink_path, **kwargs)

    n = 0
    with open(input_path, "rb") as f:
        f.seek(start)
        while f.tell() < end:
            raw = f.readline()
            if not raw:
                break
            text = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not text.strip():
                continue
            ev = _apply(config, text)
            if ev is not None:
                sink.write_one(ev)
                n += 1
    sink.close()
    return n


def apply_parallel(
    config: dict,
    input_path: Path | str,
    output_path: Optional[Path | str],
    *,
    n_workers: Optional[int] = None,
    sink_kind: str = "jsonl",
    sink_kwargs: Optional[dict] = None,
) -> int:
    """Run ``apply`` over ``input_path`` across ``n_workers`` processes.

    Returns the total event count written across all workers. See module
    docstring for output-layout details per sink kind.

    Falls back to single-process for ``sink_kind='stdout'`` (no safe way
    to interleave) or for ``n_workers <= 1``.
    """
    input_p = Path(input_path)
    if not input_p.is_file():
        raise FileNotFoundError(f"input not a regular file: {input_p}")

    n_workers = n_workers or os.cpu_count() or 4
    sink_kwargs = sink_kwargs or {}

    if sink_kind == "stdout" or n_workers <= 1:
        return _run_worker(
            config, str(input_p), 0, input_p.stat().st_size, 0,
            str(output_path) if output_path else None,
            sink_kind, sink_kwargs,
            total_workers=1,
        )

    ranges = _line_aligned_ranges(input_p, n_workers)
    if len(ranges) <= 1:
        return _run_worker(
            config, str(input_p), 0, input_p.stat().st_size, 0,
            str(output_path) if output_path else None,
            sink_kind, sink_kwargs,
        )

    output_str = str(output_path) if output_path else None
    total = 0
    with ProcessPoolExecutor(max_workers=len(ranges)) as ex:
        futures = [
            ex.submit(_run_worker,
                       config, str(input_p), start, end, i,
                       output_str, sink_kind, sink_kwargs,
                       len(ranges))
            for i, (start, end) in enumerate(ranges)
        ]
        for f in as_completed(futures):
            total += f.result()
    return total
