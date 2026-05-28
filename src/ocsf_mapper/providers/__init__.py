"""LLM providers for the mapping generator.

Users bring their own API key. Either Anthropic or OpenAI works. A ``fixture``
provider exists for offline development and CI — it reads canned responses
from disk, so tests and the generation flow can be exercised without spending
real tokens.

Selection order (highest to lowest precedence):
  1. ``OCSF_LLM_PROVIDER`` env var ("anthropic" | "openai" | "fixture")
  2. ``ANTHROPIC_API_KEY`` env var → :class:`AnthropicProvider`
  3. ``OPENAI_API_KEY`` env var → :class:`OpenAIProvider`
  4. ``RuntimeError`` (no provider configured)
"""

from __future__ import annotations

import os
from typing import Optional

from ocsf_mapper.providers.base import LLMProvider
from ocsf_mapper.providers.anthropic import AnthropicProvider
from ocsf_mapper.providers.openai import OpenAIProvider
from ocsf_mapper.providers.fixture import FixtureProvider

__all__ = [
    "LLMProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "FixtureProvider",
    "get_provider",
]


def get_provider(name: Optional[str] = None, **kwargs) -> LLMProvider:
    """Resolve an LLM provider by name or by env-detection."""
    name = (name or os.environ.get("OCSF_LLM_PROVIDER") or "").lower()
    if name == "anthropic":
        return AnthropicProvider(**kwargs)
    if name == "openai":
        return OpenAIProvider(**kwargs)
    if name == "fixture":
        return FixtureProvider(**kwargs)
    if name and name != "":
        raise ValueError(f"unknown LLM provider: {name!r}")
    # Auto-detect.
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider(**kwargs)
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIProvider(**kwargs)
    raise RuntimeError(
        "No LLM key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY, "
        "or set OCSF_LLM_PROVIDER=fixture for offline use."
    )
