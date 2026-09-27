"""Whole conversations through the turn graph: onboarding, first reading, guru turns."""

import itertools
from datetime import UTC, datetime
from typing import Any, ClassVar

from langchain_core.messages import AIMessage, BaseMessage

from guruji.agent.graph import GuruResponder
from guruji.agent.llm import ScriptedChatModel
from guruji.astro import Sky
from guruji.config import Settings
from guruji.crypto import FieldCipher, decode_key
from guruji.db.store import MemoryStore
from guruji.geo.places import PlaceIndex
from guruji.rag.cards import load_cards
from guruji.rag.embed import HashEmbedder
from guruji.rag.index import MemoryCardIndex
from guruji.rag.retrieve import Retriever
from guruji.turn.reply import Reply
from guruji.turn.worker import Turn, turn_id_for
from guruji.whatsapp.models import IncomingMessage

NOW = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)
WA_ID = "919800000001"
_ids = itertools.count()


class RecordingModel(ScriptedChatModel):
    """Scripted replies; remembers the messages each call received."""

    seen: ClassVar[list[list[BaseMessage]]] = []

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


class TalkModel(RecordingModel):
    seen: ClassVar[list[list[BaseMessage]]] = []


def _tool_call(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{next(_ids)}"}])


class Chat:
    def __init__(self, responder: GuruResponder, wa_id: str = WA_ID) -> None:
        self.responder = responder
        self.wa_id = wa_id
        self.last_turn: Turn | None = None

    async def send(
        self,
        text: str = "",
        reply_id: str | None = None,
        kind: str = "",
        media_id: str | None = None,
    ) -> Reply:
        n = next(_ids)
        msg = IncomingMessage(
            wa_id=self.wa_id,
            wamid=f"wamid.{n}",
            ts=int(NOW.timestamp()) + n,
            kind=kind or ("reply" if reply_id else "text"),  # type: ignore[arg-type]
            text=text,
            reply_id=reply_id,
            media_id=media_id,
        )
        self.last_turn = Turn(turn_id_for([msg]), self.wa_id, [msg])
        return await self.responder.respond(self.last_turn)


def _responder(
    settings: Settings,
    sky: Sky,
    places: PlaceIndex,
    script: list[AIMessage],
    talk_script: list[AIMessage] | None = None,
    **kwargs: Any,
) -> tuple[GuruResponder, MemoryStore, RecordingModel]:
    store = MemoryStore()
    RecordingModel.seen = []
    model = RecordingModel(messages=iter(script))
    TalkModel.seen = []
    talk = [TalkModel(messages=iter(talk_script))] if talk_script is not None else None
    retriever = Retriever(MemoryCardIndex(load_cards(), HashEmbedder()), HashEmbedder())
    responder = GuruResponder(
        settings,
        store,
        sky,
        places,
        [model],
        None,
        retriever,
        clock=kwargs.pop("clock", lambda: NOW),
        talk_models=talk,
        **kwargs,
    )
    return responder, store, model


def _ids_of(reply: Reply) -> list[str]:
    return [b.id for b in reply.buttons]


async def _onboard(chat: Chat) -> Reply:
    await chat.send("namaste ji")
    await chat.send("I agree", reply_id="consent_yes")
    await chat.send("Haan, 18+ hoon", reply_id="age_yes")
    await chat.send("mera naam Priya hai")
    await chat.send("15/07/1990")
    await chat.send("subah 9 baje")
    await chat.send("Aurangabad")
    await chat.send("Option 1", reply_id="place_0")
    return await chat.send("Haan, sahi hai", reply_id="confirm_yes")


FIRST_READING = [
    _tool_call(
        "record_reading",
        topic="overview",
        summary="Leo lagna, Moon in Revati; Sun mahadasha brings visibility at work.",
        factors=["Leo lagna", "Moon Revati", "Sun mahadasha"],
    ),
    AIMessage(
        "Priya ji, aapka lagna Simha hai aur Chandra Revati nakshatra mein.\n\n"
        "Abhi Surya ki mahadasha chal rahi hai. Aap kya poochna chahengi?"
    ),
]


async def test_full_onboarding_then_guru(settings: Settings, sky: Sky, places: PlaceIndex) -> None:
    script = [
        *FIRST_READING,
        _tool_call("remember_fact", category="relationship", fact="Engaged, wedding planned"),
        _tool_call("dasha_periods", from_date="2027-01-01", to_date="2027-12-31"),
        _tool_call("search_rules", query="marriage timing venus 7th house"),
        AIMessage("As an AI, I see Venus antardasha from April.\n\n**Best** window is summer."),
        AIMessage(
            "Shukra ki antardasha April se shuru hoti hai.\n\n- Garmiyon ka samay achha hai."
        ),
    ]
    responder, store, model = _responder(settings, sky, places, script)
    chat = Chat(responder)

    r = await chat.send("namaste ji")
    assert len(r.bubbles) == 2 and "Guruji" in r.bubbles[0]
    assert _ids_of(r) == ["consent_yes", "consent_notice"]
    user = next(iter(store.users.values()))
    assert user.state == "new"
    assert store.messages[user.id][0].body is None  # nothing kept before consent

    r = await chat.send("Privacy notice", reply_id="consent_notice")
    assert settings.privacy_notice_url in r.bubbles[0]
    r = await chat.send("I agree", reply_id="consent_yes")
    assert _ids_of(r) == ["age_yes", "age_no"]
    assert r.buttons[0].title == "Haan, 18+ hoon"  # button titles don't switch the language
    assert user.state == "consented"
    r = await chat.send("Haan, 18+ hoon", reply_id="age_yes")
    assert user.state == "onboarding"
    assert [c.age_confirmed for c in store.consents[user.id]] == [False, True]

    r = await chat.send("mera naam Priya hai")
    assert "Priya ji" in r.bubbles[0]
    r = await chat.send("15/07/1990")
    assert _ids_of(r) == ["time_unknown"]
    r = await chat.send("subah 9 baje")
    r = await chat.send("Aurangabad")
    assert _ids_of(r) == ["place_0", "place_1", "place_none"]
    assert "Maharashtra" in r.bubbles[0] and "Bihar" in r.bubbles[0]
    r = await chat.send("Option 1", reply_id="place_0")
    assert _ids_of(r) == ["confirm_yes", "confirm_change"]
    assert "15 July 1990" in r.bubbles[0] and "9:00 am" in r.bubbles[0]
    assert "Aurangabad, Maharashtra, India" in r.bubbles[0]
    # Birth details sit encrypted in the onboarding draft, never in clear.
    assert "1990" not in str(user.onboarding)

    r = await chat.send("Haan, sahi hai", reply_id="confirm_yes")
    assert r.bubbles == [
        "Priya ji, aapka lagna Simha hai aur Chandra Revati nakshatra mein.",
        "Abhi Surya ki mahadasha chal rahi hai. Aap kya poochna chahengi?",
    ]
    assert user.state == "active" and user.onboarding == {}
    assert (user.id, "astro-1") in store.charts
    birth = store.births[user.id]
    cipher = FieldCipher(decode_key(settings.field_encryption_key))
    assert cipher.decrypt("birth_date", birth.date_enc, user.id) == "1990-07-15"
    assert birth.tz_name == "Asia/Kolkata" and birth.time_known
    assert [r.topic for r in store.reading_log[user.id]] == ["overview"]

    # The first reading's prompt carries persona, chart and today's block.
    system = model.seen[0][0]
    blocks = system.content
    assert isinstance(blocks, list) and len(blocks) == 3
    assert "You are Guruji" in blocks[0]["text"] and blocks[0]["cache_control"]
    assert "Lagna: Leo" in blocks[1]["text"] and blocks[1]["cache_control"]
    assert "first reading" in blocks[2]["text"] and "cache_control" not in blocks[2]
    # The first reading gets the chart's core rule cards, lagna first.
    assert "RULE CARDS" in blocks[2]["text"]
    assert blocks[2]["text"].index("[lagna-leo]") < blocks[2]["text"].index("[nak-revati]")

    # A normal guru turn: tools, the style check rewrite, markdown stripped.
    r = await chat.send("meri shaadi kab hogi? engagement ho gayi hai")
    assert r.bubbles == [
        "Shukra ki antardasha April se shuru hoti hai.",
        "Garmiyon ka samay achha hai.",
    ]
    assert [f.fact for f in store.life_facts[user.id]] == ["Engaged, wedding planned"]
    tool_results = [m for m in model.seen[-1] if m.type == "tool"]
    assert any("antardasha" in str(m.content) for m in tool_results)
    assert any("method-marriage" in str(m.content) for m in tool_results)  # search_rules
    assert any("[style check]" in str(m.content) for m in model.seen[-1])
    today = model.seen[-1][0].content[2]["text"]  # type: ignore[index]
    assert "Engaged, wedding planned" not in today  # facts from this turn land next turn
    assert "Leo lagna, Moon in Revati" in today  # readings ledger is in the prompt

    # Redelivered turn: same reply, nothing run or written twice.
    assert chat.last_turn is not None
    calls = len(model.seen)
    again = await responder.respond(chat.last_turn)
    assert again.bubbles == r.bubbles
    assert len(model.seen) == calls
    assert len(store.life_facts[user.id]) == 1


async def test_everything_in_one_message_and_unknown_time(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, list(FIRST_READING))
    chat = Chat(responder)
    await chat.send("hello")
    await chat.send("I agree", reply_id="consent_yes")
    await chat.send("Yes", reply_id="age_yes")
    r = await chat.send("Rahul")
    r = await chat.send("15th July 1990 in Kochi")
    assert _ids_of(r) == ["time_unknown"]  # date taken, still needs the time
    r = await chat.send("I don't know", reply_id="time_unknown")
    assert _ids_of(r) == ["place_0", "place_1", "place_none"]  # Kochi, India or Japan
    r = await chat.send("1")
    assert "time not known" in r.bubbles[0]
    r = await chat.send("yes")
    user = next(iter(store.users.values()))
    assert user.state == "active"
    dossier = store.charts[(user.id, "astro-1")]
    assert dossier["time_known"] is False and dossier["d1"]["lagna"] is None
    assert store.births[user.id].time_enc is None


async def test_corrections_and_place_retry(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, list(FIRST_READING))
    chat = Chat(responder)
    for text, rid in [("hi", None), ("I agree", "consent_yes"), ("Yes", "age_yes")]:
        await chat.send(text, reply_id=rid)
    await chat.send("Asha")
    await chat.send("01/01/1985")
    await chat.send("10:30 pm")
    r = await chat.send("Qwertyville")
    assert "Qwertyville" in r.bubbles[0]  # not found: ask again
    r = await chat.send("Delhi")
    assert "Delhi, India" in r.bubbles[0] and "10:30 pm" in r.bubbles[0]
    r = await chat.send("Change something", reply_id="confirm_change")
    r = await chat.send("time was 11:15 pm")
    assert "11:15 pm" in r.bubbles[0] and "1 January 1985" in r.bubbles[0]
    user = next(iter(store.users.values()))
    assert user.state == "onboarding"


async def test_underage_is_declined_and_stays_declined(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, [])
    chat = Chat(responder)
    await chat.send("hi")
    await chat.send("I agree", reply_id="consent_yes")
    r = await chat.send("No", reply_id="age_no")
    assert "18+" in r.bubbles[0] or "adults" in r.bubbles[0]
    user = next(iter(store.users.values()))
    assert user.state == "blocked"
    r = await chat.send("please, my birthday is 1/1/2012")
    assert "adults" in r.bubbles[0]
    assert not store.births


async def test_chart_error_goes_back_to_fixing(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, [])
    chat = Chat(responder)
    for text, rid in [("hi", None), ("I agree", "consent_yes"), ("Yes", "age_yes")]:
        await chat.send(text, reply_id=rid)
    for text in ("Ola", "01/01/1985", "10:30 am", "Tromso"):
        r = await chat.send(text)
    r = await chat.send("Yes, correct", reply_id="confirm_yes")
    assert "poles" in r.bubbles[0]
    user = next(iter(store.users.values()))
    assert user.state == "onboarding" and not store.charts
    r = await chat.send("place is London")
    assert "London, England" in r.bubbles[0]


async def test_hindi_user_gets_devanagari(settings: Settings, sky: Sky, places: PlaceIndex) -> None:
    responder, _, _ = _responder(settings, sky, places, [])
    chat = Chat(responder)
    r = await chat.send("नमस्ते गुरुजी")
    assert "गुरुजी" in r.bubbles[0]
    assert [b.title for b in r.buttons] == ["सहमत हूँ", "गोपनीयता सूचना"]


async def test_voice_note_before_voice_support(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, _, _ = _responder(settings, sky, places, list(FIRST_READING))
    chat = Chat(responder)
    for text, rid in [("hi", None), ("I agree", "consent_yes"), ("Yes", "age_yes")]:
        await chat.send(text, reply_id=rid)
    r = await chat.send(kind="audio")
    assert "typed" in r.bubbles[0]


async def test_small_talk_goes_to_the_talk_model(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, _, reading = _responder(
        settings,
        sky,
        places,
        list(FIRST_READING),
        talk_script=[AIMessage("Aapka swagat hai, Priya ji 🙏")],
    )
    chat = Chat(responder)
    await _onboard(chat)
    reading_calls = len(reading.seen)
    r = await chat.send("thank you")
    assert r.bubbles == ["Aapka swagat hai, Priya ji 🙏"]
    assert len(reading.seen) == reading_calls  # the reading model was not used
    assert len(TalkModel.seen) == 1
    talk_prompt = TalkModel.seen[0][0].content[2]["text"]  # type: ignore[index]
    assert "Reply language: Hinglish" in talk_prompt
    assert "RULE CARDS" not in talk_prompt  # no retrieval for small talk
