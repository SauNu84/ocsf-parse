"""Sink Protocol and a small base class with sensible defaults."""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable


@runtime_checkable
class Sink(Protocol):
    """Output destination for OCSF events."""

    def write_one(self, event: dict) -> None: ...
    def write_many(self, events: Iterable[dict]) -> int: ...
    def close(self) -> None: ...
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, tb): ...


class _SinkBase:
    """Shared scaffolding: implements :meth:`write_many` in terms of :meth:`write_one`,
    plus the context-manager methods.
    """

    def write_one(self, event: dict) -> None:  # pragma: no cover - subclasses override
        raise NotImplementedError

    def write_many(self, events: Iterable[dict]) -> int:
        n = 0
        for ev in events:
            self.write_one(ev)
            n += 1
        return n

    def close(self) -> None:  # pragma: no cover - default no-op
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
