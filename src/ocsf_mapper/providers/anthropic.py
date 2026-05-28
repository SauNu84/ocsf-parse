"""Anthropic Claude provider. Requires the ``anthropic`` SDK and an API key."""

from __future__ import annotations

import os
from typing import Optional


class AnthropicProvider:
    name = "anthropic"
    default_model = "claude-opus-4-7"

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as e:  # pragma: no cover - env-specific
            raise ImportError(
                "AnthropicProvider requires the `anthropic` SDK. "
                "Install with: pip install ocsf-mapper[anthropic]"
            ) from e
        self.model = model or self.default_model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:  # pragma: no cover - env-specific
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Export it or pass api_key=."
            )

    def complete(self, prompt: str, system: str = "", max_tokens: int = 8000) -> str:
        import anthropic  # noqa: F401
        client = __import__("anthropic").Anthropic(api_key=self._api_key)
        resp = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "You produce strict JSON, no commentary.",
            messages=[{"role": "user", "content": prompt}],
        )
        # anthropic SDK returns a list of content blocks; the first is text.
        return resp.content[0].text
