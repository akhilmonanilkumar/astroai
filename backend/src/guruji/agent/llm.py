"""Chat model factory. Models are "provider:model" strings, swappable from settings.

Default setup (Sarvam, Indian-language models, OpenAI-compatible API):
- Reading model (real work: readings, timing, remedies): sarvam-105b, a reasoning model,
  with thinking off by default (REASONING_EFFORT=none): at "low" it still reasoned for
  6000+ tokens (30+ s) on a reading, far beyond the WhatsApp latency budget.
- Talk model (greetings, thanks, small talk, clarifying back-and-forth), turn routing and
  onboarding extraction: sarvam-105b-conversations (no reasoning, ~0.5 s).

"sail:<model>" runs open models (GLM, Kimi, DeepSeek) on Sail Research's OpenAI-compatible
API, e.g. sail:zai-org/GLM-5.3. "anthropic:<model>" is also supported. "fake" = scripted
replies with no API calls, used in dev when no key is configured and in tests.
"""

import logging
import os
from collections.abc import Iterator
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from guruji.config import Settings

log = logging.getLogger(__name__)

FAKE = "fake"
_ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Sarvam's reasoning model spends tokens thinking before it answers: leave room.
_READING_MAX_TOKENS = 1500
_TALK_MAX_TOKENS = 1000
# With thinking on, sarvam-105b can reason for 6000+ tokens before answering.
_THINKING_MAX_TOKENS = 12000


def _fake_replies() -> Iterator[AIMessage]:
    while True:
        yield AIMessage(
            content="🙏 Guruji is resting right now (no LLM configured in this dev setup). "
            "Your chart is saved and ready.\n\nSet SARVAM_API_KEY to hear a real reading."
        )


class ScriptedChatModel(GenericFakeChatModel):
    """Replays scripted AIMessages (tool calls included); accepts any tools."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self


def fake_model() -> BaseChatModel:
    return ScriptedChatModel(messages=_fake_replies())


def _has_key(provider: str, settings: Settings) -> bool:
    if provider == "sarvam":
        return settings.sarvam_api_key is not None
    if provider == "sail":
        return settings.sail_api_key is not None
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return True


def resolve(name: str, settings: Settings) -> str:
    """Fall back to the fake model in dev/test when the provider's key is missing."""
    provider = name.partition(":")[0]
    if name != FAKE and settings.env in ("dev", "test") and not _has_key(provider, settings):
        log.warning("no API key for %s: using the fake model for %s", provider, name)
        return FAKE
    return name


def is_anthropic(name: str) -> bool:
    return name.startswith("anthropic:")


def _sarvam(model: str, settings: Settings, *, reading: bool) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    assert settings.sarvam_api_key is not None
    # Sent in the body as-is: langchain would rename max_tokens to max_completion_tokens,
    # which Sarvam ignores (it then caps output at 2048, reasoning included).
    body: dict[str, Any] = {"max_tokens": _READING_MAX_TOKENS if reading else _TALK_MAX_TOKENS}
    if model == "sarvam-105b":  # the reasoning model; None switches thinking off
        effort = settings.reasoning_effort
        body["reasoning_effort"] = None if effort == "none" else effort
        if effort != "none":
            body["max_tokens"] = _THINKING_MAX_TOKENS
    return ChatOpenAI(
        model=model,
        base_url=settings.sarvam_base_url,
        api_key=settings.sarvam_api_key,
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
        extra_body=body,
    )


def _sail(model: str, settings: Settings, *, reading: bool) -> BaseChatModel:
    """Sail rejects stop, seed and the penalty parameters; none of them are sent."""
    from langchain_openai import ChatOpenAI

    assert settings.sail_api_key is not None
    body: dict[str, Any] = {}
    if settings.sail_reasoning_effort:
        body["reasoning_effort"] = settings.sail_reasoning_effort
    return ChatOpenAI(
        model=model,
        base_url=settings.sail_base_url,
        api_key=settings.sail_api_key,
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
        max_tokens=_READING_MAX_TOKENS if reading else _TALK_MAX_TOKENS,
        extra_body=body or None,
    )


def _anthropic(model: str, settings: Settings, *, reading: bool) -> BaseChatModel:
    kwargs: dict[str, Any] = {
        "timeout": settings.llm_timeout_seconds,
        "max_retries": 2,
        "max_tokens": _READING_MAX_TOKENS if reading else _TALK_MAX_TOKENS,
    }
    if reading and model.startswith("claude-opus-5"):
        kwargs["output_config"] = {"effort": "medium"}
        # Server-side refusal fallback: a declined request is re-run on a fallback model.
        kwargs["betas"] = [_ANTHROPIC_FALLBACK_BETA]
        kwargs["model_kwargs"] = {"fallbacks": "default"}
    chat: BaseChatModel = init_chat_model(model, model_provider="anthropic", **kwargs)
    return chat


def make_model(name: str, settings: Settings, *, reading: bool) -> BaseChatModel:
    name = resolve(name, settings)
    if name == FAKE:
        return fake_model()
    provider, _, model = name.partition(":")
    if provider == "sarvam":
        return _sarvam(model, settings, reading=reading)
    if provider == "sail":
        return _sail(model, settings, reading=reading)
    if provider == "anthropic":
        return _anthropic(model, settings, reading=reading)
    chat: BaseChatModel = init_chat_model(model, model_provider=provider)
    return chat


def _with_fallback(
    primary: str, fallback: str, settings: Settings, *, reading: bool
) -> list[BaseChatModel]:
    models = [make_model(primary, settings, reading=reading)]
    if fallback and fallback != primary:
        models.append(make_model(fallback, settings, reading=reading))
    return models


def reading_models(settings: Settings) -> list[BaseChatModel]:
    """The model for real work (readings), then its fallback."""
    return _with_fallback(settings.guru_model, settings.guru_fallback_model, settings, reading=True)


def talk_models(settings: Settings) -> list[BaseChatModel]:
    """The model for conversation (small talk, clarifying), then its fallback."""
    return _with_fallback(settings.talk_model, settings.guru_model, settings, reading=False)


def fast_model(settings: Settings) -> BaseChatModel | None:
    """Routing and extraction model, or None when faked (rules only)."""
    name = resolve(settings.fast_model, settings)
    return None if name == FAKE else make_model(name, settings, reading=False)


def cache_prompt_blocks(settings: Settings) -> bool:
    """Anthropic takes the system prompt as cached blocks; OpenAI-style APIs want a string."""
    return is_anthropic(settings.guru_model) and is_anthropic(settings.talk_model)
