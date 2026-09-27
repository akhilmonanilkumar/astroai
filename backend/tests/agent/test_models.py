from typing import Any

import pytest
from pydantic import SecretStr

from guruji.agent.guru import GuruContext, system_message
from guruji.agent.llm import (
    cache_prompt_blocks,
    fake_model,
    make_model,
    reading_models,
    talk_models,
)
from guruji.agent.router import Decision, obvious_small_talk, route_turn
from guruji.config import Settings


@pytest.mark.parametrize(
    ("text", "small"),
    [
        ("ok", True),
        ("thank you ji", False),  # "ji" is left to the model
        ("Thank you", True),
        ("dhanyavaad", True),
        ("🙏", True),
        ("धन्यवाद", True),
        ("haan", False),  # may answer a clarifying question
        ("meri shaadi kab hogi", False),
        ("ok but when will I get the job?", False),
    ],
)
def test_obvious_small_talk(text: str, small: bool) -> None:
    assert obvious_small_talk(text) is small


class _Structured:
    def __init__(self, result: Any) -> None:
        self.result = result

    async def ainvoke(self, prompt: str) -> Any:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Router:
    def __init__(self, kind: str | Exception, safety: str = "none") -> None:
        self.kind = kind
        self.safety = safety
        self.calls = 0

    def with_structured_output(self, schema: Any, **kw: Any) -> _Structured:
        self.calls += 1
        if isinstance(self.kind, Exception):
            return _Structured(self.kind)
        return _Structured(schema(kind=self.kind, safety=self.safety))


async def test_route_turn() -> None:
    talk = _Router("talk")
    assert await route_turn(talk, "thanks", None) == Decision("talk")
    assert talk.calls == 0  # rules decided, no model call
    assert (await route_turn(_Router("reading"), "meri naukri kab lagegi", None)).route == "reading"
    assert (
        await route_turn(_Router("talk"), "aaj mood thoda off hai", "Kaise hain?")
    ).route == "talk"
    assert await route_turn(_Router(RuntimeError("down")), "career?", None) == Decision("reading")
    assert await route_turn(None, "career?", None) == Decision("reading")


async def test_route_turn_flags_safety() -> None:
    d = await route_turn(_Router("talk", "crisis"), "sab khatam karne ka mann hai", None)
    assert d.safety == "crisis"


def _sarvam_settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, env="test", sarvam_api_key=SecretStr("k"), **kw)  # type: ignore[call-arg]


def test_sarvam_models() -> None:
    s = _sarvam_settings()
    reading = reading_models(s)
    assert [m.model_name for m in reading] == ["sarvam-105b", "sarvam-105b-conversations"]  # type: ignore[attr-defined]
    body = reading[0].extra_body  # type: ignore[attr-defined]
    assert body == {"max_tokens": 1500, "reasoning_effort": None}  # thinking off by default
    # Sent as max_tokens, not max_completion_tokens (which Sarvam ignores).
    payload = reading[0]._get_request_payload([("user", "hi")])  # type: ignore[attr-defined]
    assert "max_completion_tokens" not in payload and "max_tokens" not in payload
    assert str(reading[0].openai_api_base).startswith("https://api.sarvam.ai")  # type: ignore[attr-defined]
    talk = talk_models(s)
    assert talk[0].model_name == "sarvam-105b-conversations"  # type: ignore[attr-defined]
    assert talk[0].extra_body == {"max_tokens": 1000}  # type: ignore[attr-defined]
    thinking = reading_models(_sarvam_settings(reasoning_effort="low"))[0]
    assert thinking.extra_body == {"max_tokens": 12000, "reasoning_effort": "low"}  # type: ignore[attr-defined]
    assert not cache_prompt_blocks(s)  # Sarvam wants the system prompt as one string


def test_no_key_in_dev_means_fake() -> None:
    s = Settings(_env_file=None, env="test")  # type: ignore[call-arg]
    assert type(make_model(s.guru_model, s, reading=True)) is type(fake_model())


def test_system_prompt_string_for_sarvam(sky: Any) -> None:
    from datetime import UTC, date, datetime, time

    from guruji.astro import BirthInput, compute_dossier
    from guruji.geo.tz import birth_moment

    birth = BirthInput(
        moment=birth_moment(date(1990, 7, 15), time(9, 0), "Asia/Kolkata"),
        latitude=28.6,
        longitude=77.2,
    )
    now = datetime(2026, 9, 28, tzinfo=UTC)
    ctx = GuruContext(
        sky, compute_dossier(sky, birth, now), now, "Asha", [], [], language="hinglish"
    )
    msg = system_message(ctx, blocks=False)
    assert isinstance(msg.content, str)
    assert msg.content.index("You are Guruji") < msg.content.index("BIRTH CHART")
    assert "Do not use Devanagari" in msg.content
    assert isinstance(system_message(ctx).content, list)
