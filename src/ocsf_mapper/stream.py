"""``tail -f``-style streaming for live log → OCSF event piping.

No third-party deps — just a polling loop on ``readline()``. Lines that
don't end in a newline yet are buffered and yielded once they do.

Public API:

    tail_file(path, poll_interval=0.5, from_start=False, stop=None)
        → Iterator[str]   newline-terminated lines as they arrive

    stream_apply(config, path, sink, **tail_kwargs) -> None
        runs ``tail_file`` through ``apply`` and into ``sink.write_one``

Both honour an optional ``threading.Event`` ``stop`` so callers can break
the loop deterministically (used by the test suite and by Ctrl+C handling
in the CLI).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Iterator, Optional


def tail_file(
    path: Path | str,
    *,
    poll_interval: float = 0.5,
    from_start: bool = False,
    stop: Optional[threading.Event] = None,
) -> Iterator[str]:
    """Yield each newline-terminated line as it appears in ``path``.

    Behaves like ``tail -f``: when there's nothing new, sleeps
    ``poll_interval`` seconds and tries again. Returns when ``stop`` is set
    (or never, if it is None).

    Buffers trailing partial lines (without a final newline) and only yields
    them once the newline arrives. After an empty read we re-seek to the
    current offset, which forces TextIOWrapper to drop its EOF flag so it
    will pick up bytes appended after we hit EOF.
    """
    p = Path(path)
    with p.open("r", encoding="utf-8", errors="replace") as f:
        if not from_start:
            f.seek(0, 2)  # to EOF
        buf = ""
        while True:
            if stop is not None and stop.is_set():
                if buf:
                    yield buf
                return
            line = f.readline()
            if not line:
                # Refresh: re-seek to current position so subsequent reads
                # see any bytes the writer has appended since we hit EOF.
                f.seek(f.tell())
                time.sleep(poll_interval)
                continue
            if line.endswith("\n"):
                yield (buf + line)
                buf = ""
            else:
                buf += line


def stream_apply(
    config: dict,
    path: Path | str,
    sink,
    *,
    poll_interval: float = 0.5,
    from_start: bool = False,
    stop: Optional[threading.Event] = None,
) -> int:
    """Run ``tail_file`` lines through ``apply`` into ``sink``.

    Returns the count of events emitted when the loop exits. ``sink`` must
    expose ``write_one(event: dict) -> None``; the standard
    :mod:`ocsf_mapper.sinks` classes all satisfy this.
    """
    from ocsf_mapper.apply import apply

    n = 0
    for raw in tail_file(path, poll_interval=poll_interval,
                          from_start=from_start, stop=stop):
        event = apply(config, raw.rstrip("\n"))
        if event is not None:
            sink.write_one(event)
            n += 1
    return n
