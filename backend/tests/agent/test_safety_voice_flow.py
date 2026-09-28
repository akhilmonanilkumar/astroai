"""Safety and voice through the whole turn graph."""

from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import AIMessage

from guruji.astro import Sky
from guruji.config import Settings
from guruji.geo.places import PlaceIndex
from guruji.safety.messages import HOLDING, REPLIES, SAFE_FALLBACK
from guruji.voice.speech import SpeechError, Transcript

from .test_conversation import FIRST_READING, Chat, TalkModel, _onboard, _responder


def _real_now() -> datetime:
    return datetime.now(UTC)


class FakeSpeech:
    def __init__(self, transcript: str = "", fail: bool = False) -> None:
        self.transcript = transcript
        self.fail = fail
        self.heard: list[tuple[bytes, str]] = []

    async def transcribe(self, audio: bytes, mime: str) -> Transcript:
        self.heard.append((audio, mime))
        if self.fail:
            raise SpeechError("down")
        return Transcript(self.transcript, "hi-IN")

    async def synthesize(self, text: str, language_code: str) -> bytes:
        return b"OggS" + text.encode()


class FakeMedia:
    def __init__(self) -> None:
        self.downloads: list[str] = []

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        self.downloads.append(media_id)
        return b"OggS-voice", "audio/ogg; codecs=opus"


class _Decider:
    """A fast model stand-in for routing; returns a fixed decision."""

    def __init__(self, kind: str = "reading", safety: str = "none") -> None:
        self.kind, self.safety = kind, safety

    def with_structured_output(self, schema: Any, **kw: Any) -> Any:
        outer = self

        class _S:
            async def ainvoke(self, prompt: str) -> Any:
                return schema(kind=outer.kind, safety=outer.safety)

        return _S()


async def test_crisis_before_consent_gets_help_and_escalates(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, [], clock=_real_now)
    chat = Chat(responder)
    r = await chat.send("i don't want to live anymore")
    assert r.bubbles == REPLIES["crisis"]["en"]
    assert r.kind == "safety" and r.alert_escalation_id
    user = next(iter(store.users.values()))
    assert user.state == "escalated"
    esc = await store.active_escalation(user.id)
    assert esc is not None and esc.category == "crisis" and esc.severity == 1
    assert esc.id == r.alert_escalation_id
    assert store.messages[user.id][0].body is None  # still nothing kept before consent
    # Resolving returns them to where they were: not yet consented.
    assert await store.resolve_escalation(esc.id, hand_back=True)
    assert user.state == "new"
    r = await chat.send("hi")
    assert [b.id for b in r.buttons] == ["consent_yes", "consent_notice"]


async def test_escalated_chat_holds_then_goes_quiet_but_never_ignores_a_crisis(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, list(FIRST_READING), clock=_real_now)
    chat = Chat(responder)
    await _onboard(chat)
    user = next(iter(store.users.values()))
    r = await chat.send("mujhe kisi insaan se baat karni hai")
    assert r.bubbles == REPLIES["human_requested"]["hinglish"]
    assert user.state == "escalated"
    r = await chat.send("kab tak reply aayega?")
    assert r.bubbles == [HOLDING["hinglish"]] and r.kind == "holding"
    r = await chat.send("hello??")
    assert r.bubbles == []  # held within the last few hours: stay quiet
    r = await chat.send("ab jeena nahi chahti")
    assert r.bubbles == REPLIES["crisis"]["hinglish"]
    esc = await store.active_escalation(user.id)
    assert esc is not None and r.alert_escalation_id == esc.id  # same case, alerted again
    assert await store.resolve_escalation(esc.id, hand_back=True)
    assert user.state == "active"


async def test_model_second_opinion_escalates(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, list(FIRST_READING), clock=_real_now)
    responder.extractor = _Decider("talk", "crisis")  # type: ignore[assignment]
    chat = Chat(responder)
    await _onboard(chat)
    r = await chat.send("sab khatam kar dene ka mann hai")  # no rule matches this phrasing
    assert r.bubbles == REPLIES["crisis"]["hinglish"]
    assert next(iter(store.users.values())).state == "escalated"


async def test_guardrail_fallback_after_failed_rewrite(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    bad = "Aapki shaadi 100% guaranteed hai March mein."
    script = [
        *FIRST_READING,
        AIMessage(bad),
        AIMessage("Pakka hoga, guarantee hai."),  # the rewrite still breaks the rule
    ]
    responder, _, model = _responder(settings, sky, places, script, clock=_real_now)
    chat = Chat(responder)
    await _onboard(chat)
    r = await chat.send("meri shaadi kab hogi")
    assert r.bubbles == [SAFE_FALLBACK["hinglish"]]
    assert any("[style check]" in str(m.content) for m in model.seen[-1])


async def test_voice_note_in_voice_note_out(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    speech, media = FakeSpeech("मेरी शादी कब होगी"), FakeMedia()
    script = [*FIRST_READING, AIMessage("आपकी शादी के योग अगले साल अप्रैल से बनते हैं।")]
    responder, store, model = _responder(
        settings, sky, places, script, clock=_real_now, speech=speech, media=media
    )
    chat = Chat(responder)
    await _onboard(chat)
    r = await chat.send(kind="audio", media_id="media-1")
    assert media.downloads == ["media-1"]
    assert r.voice_language == "hi-IN"
    assert r.bubbles == ["आपकी शादी के योग अगले साल अप्रैल से बनते हैं।"]
    today = model.seen[-1][0].content[2]["text"]  # type: ignore[index]
    assert "voice note" in today and "Hindi in Devanagari" in today
    user = next(iter(store.users.values()))
    heard = store.messages[user.id][-2]
    assert (heard.kind, heard.body) == ("audio", "मेरी शादी कब होगी")


async def test_voice_before_consent_is_heard_for_safety_only(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    """Before consent a voice note is transcribed only to check for a crisis: nothing is
    kept and nothing else uses it."""
    speech, media = FakeSpeech("hello"), FakeMedia()
    responder, store, _ = _responder(settings, sky, places, [], speech=speech, media=media)
    chat = Chat(responder)
    await chat.send("hi")
    r = await chat.send(kind="audio", media_id="media-0")
    assert media.downloads == ["media-0"]
    assert [b.id for b in r.buttons] == ["consent_yes", "consent_notice"]
    user = next(iter(store.users.values()))
    assert all(m.body is None for m in store.messages[user.id] if m.direction == "in")


async def test_crisis_voice_note_before_consent_gets_help(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    speech, media = FakeSpeech("मैं जीना नहीं चाहता"), FakeMedia()
    responder, store, _ = _responder(
        settings, sky, places, [], clock=_real_now, speech=speech, media=media
    )
    chat = Chat(responder)
    r = await chat.send(kind="audio", media_id="media-9")
    assert r.bubbles == REPLIES["crisis"]["hi"]
    assert r.kind == "safety" and r.alert_escalation_id
    user = next(iter(store.users.values()))
    assert user.state == "escalated"
    assert all(m.body is None for m in store.messages[user.id] if m.direction == "in")


async def test_failed_transcription_asks_to_type(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, _, _ = _responder(
        settings,
        sky,
        places,
        list(FIRST_READING),
        speech=FakeSpeech(fail=True),
        media=FakeMedia(),
    )
    chat = Chat(responder)
    await _onboard(chat)
    r = await chat.send(kind="audio", media_id="m")
    assert "hear" in r.bubbles[0] or "sunai" in r.bubbles[0]
    assert r.voice_language is None


async def test_small_talk_voice_uses_talk_model(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, _, _ = _responder(
        settings,
        sky,
        places,
        list(FIRST_READING),
        talk_script=[AIMessage("Dhanyavaad ji 🙏")],
        speech=FakeSpeech("thank you"),
        media=FakeMedia(),
    )
    chat = Chat(responder)
    await _onboard(chat)
    r = await chat.send(kind="audio", media_id="m")
    assert r.bubbles == ["Dhanyavaad ji 🙏"] and r.voice_language == "hi-IN"
    assert len(TalkModel.seen) == 1


def test_holding_interval_is_hours() -> None:
    from guruji.agent.graph import HOLDING_EVERY

    assert timedelta(hours=1) <= HOLDING_EVERY <= timedelta(hours=24)


async def test_no_holding_reply_while_the_team_is_writing(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, [], clock=_real_now)
    chat = Chat(responder)
    await chat.send("i don't want to live anymore")
    user = next(iter(store.users.values()))
    meta = {"kind": "human", "bubbles": ["hi"], "buttons": []}
    await store.log_human_message(
        user.id, "h_1", kind="text", body="Guruji team: hi", meta=meta, admin_id="a1"
    )
    r = await chat.send("hello?")
    assert r.bubbles == []  # a person is answering: no "our team will reply" on top


async def test_voice_replies_can_be_switched_off_from_config(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(
        settings,
        sky,
        places,
        list(FIRST_READING),
        talk_script=[AIMessage("Dhanyavaad ji 🙏")],
        speech=FakeSpeech("thank you"),
        media=FakeMedia(),
    )
    await store.set_config("flags", {"voice_enabled": False}, "admin:1")
    chat = Chat(responder)
    await _onboard(chat)
    r = await chat.send(kind="audio", media_id="m")
    assert r.bubbles == ["Dhanyavaad ji 🙏"] and r.voice_language is None
