"""OpenAI GPT provider. Requires the ``openai`` SDK and an API key."""

from __future__ import annotations

import os
from typing import Optional


class OpenAIProvider:
    name = "openai"
    default_model = "gpt-4o"

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as e:  # pragma: no cover - env-specific
            raise ImportError(
                "OpenAIProvider requires the `openai` SDK. "
                "Install with: pip install ocsf-mapper[openai]"
            ) from e
        self.model = model or self.default_model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:  # pragma: no cover - env-specific
            raise RuntimeError(
                "OPENAI_API_KEY not set. Export it or pass api_key=."
            )

    def complete(self, prompt: str, system: str = "", max_tokens: int = 8000) -> str:
        client = __import__("openai").OpenAI(api_key=self._api_key)
        # OpenAI's JSON mode requires the word "JSON" somewhere in the prompt.
        # The generator's prompt builder already includes it; we just enable
        # response_format here.
        resp = client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system or "You produce strict JSON, no commentary."},
                {"role": "user",   "content": prompt},
            ],
        )
        return resp.choices[0].message.content or ""
