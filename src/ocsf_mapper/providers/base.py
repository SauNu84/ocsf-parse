"""LLMProvider Protocol — the contract for chat-completion backends."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Minimum surface a provider must expose to the mapping generator."""

    name: str
    default_model: str

    def complete(self, prompt: str, system: str = "", max_tokens: int = 8000) -> str:
        """Return the model's text response to ``prompt`` with optional system message.

        Implementations are expected to return raw text — the caller does its own
        JSON parsing. Network/API errors should be raised, not swallowed.
        """
        ...
