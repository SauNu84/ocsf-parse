"""Mocked tests for Anthropic / OpenAI providers.

These exercise the provider modules without making real API calls.
The pattern: inject a fake ``anthropic`` / ``openai`` module into
``sys.modules`` before constructing the provider, so the provider's
internal imports pick up the mock.

Covers:
  - init failure when env var missing (no API key)
  - init success when env var present
  - complete() shape: SDK call args + return-value extraction
  - model override via constructor param
"""

from __future__ import annotations

import sys
import types

import pytest


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Inject a stub `anthropic` module into sys.modules."""
    fake = types.ModuleType("anthropic")

    class _FakeContentBlock:
        def __init__(self, text):
            self.text = text

    class _FakeResponse:
        def __init__(self, text):
            self.content = [_FakeContentBlock(text)]

    class _FakeMessages:
        def __init__(self, parent):
            self._parent = parent

        def create(self, **kwargs):
            self._parent.last_call = kwargs
            return _FakeResponse(self._parent.canned_reply)

    class FakeAnthropic:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.canned_reply = "FAKE-REPLY"
            self.last_call = None
            self.messages = _FakeMessages(self)

    fake.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return fake


def test_anthropic_init_requires_api_key(monkeypatch, fake_anthropic):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from ocsf_mapper.providers.anthropic import AnthropicProvider
    with pytest.raises(RuntimeError) as exc:
        AnthropicProvider()
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_anthropic_init_with_explicit_api_key(fake_anthropic, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from ocsf_mapper.providers.anthropic import AnthropicProvider
    p = AnthropicProvider(api_key="sk-test")
    assert p._api_key == "sk-test"
    assert p.model == AnthropicProvider.default_model


def test_anthropic_complete_returns_first_content_block(fake_anthropic, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    from ocsf_mapper.providers.anthropic import AnthropicProvider
    p = AnthropicProvider()
    out = p.complete("hello", system="be brief", max_tokens=42)
    assert out == "FAKE-REPLY"


def test_anthropic_complete_forwards_call_arguments(fake_anthropic, monkeypatch):
    """The SDK should see our prompt, system, model, max_tokens verbatim."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    from ocsf_mapper.providers.anthropic import AnthropicProvider

    # Capture by patching the class to be its own factory + reading after.
    captured = {}

    class _CaptureClient(fake_anthropic.Anthropic):
        def __init__(self, api_key=None):
            super().__init__(api_key=api_key)
            captured["client"] = self

    fake_anthropic.Anthropic = _CaptureClient
    p = AnthropicProvider(model="claude-sonnet-test")
    p.complete("ask away", system="rules", max_tokens=123)
    call = captured["client"].last_call
    assert call["model"] == "claude-sonnet-test"
    assert call["max_tokens"] == 123
    assert call["system"] == "rules"
    assert call["messages"][0]["content"] == "ask away"


def test_anthropic_complete_default_system_prompt(fake_anthropic, monkeypatch):
    """When system='' we substitute the default 'strict JSON' system prompt."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    from ocsf_mapper.providers.anthropic import AnthropicProvider

    captured = {}

    class _CaptureClient(fake_anthropic.Anthropic):
        def __init__(self, api_key=None):
            super().__init__(api_key=api_key)
            captured["client"] = self

    fake_anthropic.Anthropic = _CaptureClient
    p = AnthropicProvider()
    p.complete("hi")  # no system arg
    assert "strict JSON" in captured["client"].last_call["system"]


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_openai(monkeypatch):
    fake = types.ModuleType("openai")

    class _FakeMessage:
        def __init__(self, content):
            self.content = content

    class _FakeChoice:
        def __init__(self, content):
            self.message = _FakeMessage(content)

    class _FakeResponse:
        def __init__(self, content):
            self.choices = [_FakeChoice(content)]

    class _FakeChatCompletions:
        def __init__(self, parent):
            self._parent = parent

        def create(self, **kwargs):
            self._parent.last_call = kwargs
            return _FakeResponse(self._parent.canned_reply)

    class _FakeChat:
        def __init__(self, parent):
            self.completions = _FakeChatCompletions(parent)

    class FakeOpenAI:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.canned_reply = '{"hello": "world"}'
            self.last_call = None
            self.chat = _FakeChat(self)

    fake.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake)
    return fake


def test_openai_init_requires_api_key(monkeypatch, fake_openai):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from ocsf_mapper.providers.openai import OpenAIProvider
    with pytest.raises(RuntimeError):
        OpenAIProvider()


def test_openai_init_with_explicit_api_key(fake_openai, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from ocsf_mapper.providers.openai import OpenAIProvider
    p = OpenAIProvider(api_key="sk-test", model="gpt-test")
    assert p._api_key == "sk-test"
    assert p.model == "gpt-test"


def test_openai_complete_returns_first_choice_content(fake_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    from ocsf_mapper.providers.openai import OpenAIProvider
    p = OpenAIProvider()
    out = p.complete("hi", system="be brief")
    assert out == '{"hello": "world"}'


def test_openai_complete_forces_json_object_mode(fake_openai, monkeypatch):
    """The OpenAI provider should always request JSON mode (the prompt builder
    is responsible for including 'JSON' in the user message itself)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    from ocsf_mapper.providers.openai import OpenAIProvider

    captured = {}

    class _CaptureClient(fake_openai.OpenAI):
        def __init__(self, api_key=None):
            super().__init__(api_key=api_key)
            captured["client"] = self

    fake_openai.OpenAI = _CaptureClient
    p = OpenAIProvider(model="gpt-4o")
    p.complete("emit JSON please", system="rules")
    call = captured["client"].last_call
    assert call["response_format"] == {"type": "json_object"}
    assert call["model"] == "gpt-4o"
    # Two messages: system + user
    assert len(call["messages"]) == 2
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][1]["role"] == "user"


def test_openai_complete_default_system_prompt(fake_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    from ocsf_mapper.providers.openai import OpenAIProvider

    captured = {}

    class _CaptureClient(fake_openai.OpenAI):
        def __init__(self, api_key=None):
            super().__init__(api_key=api_key)
            captured["client"] = self

    fake_openai.OpenAI = _CaptureClient
    p = OpenAIProvider()
    p.complete("hi")  # no system arg
    assert "strict JSON" in captured["client"].last_call["messages"][0]["content"]


def test_openai_complete_handles_null_content(fake_openai, monkeypatch):
    """Some OpenAI responses can have content=None — the provider should
    return an empty string rather than crash."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    from ocsf_mapper.providers.openai import OpenAIProvider

    # Override the canned reply factory to produce None.
    class _NullContentClient(fake_openai.OpenAI):
        def __init__(self, api_key=None):
            super().__init__(api_key=api_key)
            self.canned_reply = None

    fake_openai.OpenAI = _NullContentClient
    p = OpenAIProvider()
    assert p.complete("hi") == ""


# ---------------------------------------------------------------------------
# get_provider() routing — both branches
# ---------------------------------------------------------------------------


def test_get_provider_anthropic_via_env(fake_anthropic, monkeypatch):
    """ANTHROPIC_API_KEY env → AnthropicProvider."""
    monkeypatch.delenv("OCSF_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from ocsf_mapper.providers import get_provider
    p = get_provider()
    assert p.name == "anthropic"


def test_get_provider_openai_via_env(fake_openai, monkeypatch):
    """OPENAI_API_KEY env → OpenAIProvider when no Anthropic key."""
    monkeypatch.delenv("OCSF_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    from ocsf_mapper.providers import get_provider
    p = get_provider()
    assert p.name == "openai"


def test_get_provider_anthropic_explicit_name(fake_anthropic, monkeypatch):
    monkeypatch.delenv("OCSF_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    from ocsf_mapper.providers import get_provider
    p = get_provider(name="anthropic")
    assert p.name == "anthropic"


def test_get_provider_openai_explicit_name(fake_openai, monkeypatch):
    monkeypatch.delenv("OCSF_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    from ocsf_mapper.providers import get_provider
    p = get_provider(name="openai")
    assert p.name == "openai"
