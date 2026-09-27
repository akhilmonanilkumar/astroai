"""The turn graph (LangGraph): who is this, where are they, what do we say.

    load ─┬─ replay (turn already committed: resend the stored reply)
          ├─ safety (crisis / emergency / asks for a human: scripted reply, escalate) ─ commit
          ├─ escalated (a human is handling it: holding reply or silence) ─ commit
          ├─ onboarding ─┬─ cast_chart ─ guru (first reading) ─ commit
          │              └─ commit
          ├─ guru ─ commit          (may divert to safety on the model's second opinion)
          └─ silent (blocked) ─ commit

`load` also transcribes voice notes (after consent), so everything downstream sees text.
Consent, onboarding and safety replies are code, not prompts. Everything a turn changes
is committed at the end in one idempotent write keyed by turn_id.
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Protocol, TypedDict
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from guruji.agent.copy import LINES
from guruji.agent.guru import GuruContext, build_guru, run_guru
from guruji.agent.language import Language
from guruji.agent.language import update as update_language
from guruji.agent.onboarding import Draft, Onboarding, OnboardingDeps
from guruji.agent.router import Route as ModelRoute
from guruji.agent.router import route_turn
from guruji.astro import (
    BirthInput,
    Dossier,
    OutOfRangeError,
    Sky,
    compute_dossier,
    transit_snapshot,
)
from guruji.astro.dossier import ENGINE_VERSION
from guruji.config import Settings
from guruji.crypto import FieldCipher, decode_key, lookup_hash
from guruji.db.models import EncryptedBirth, EscalationOpen, InboundLog, TurnWrite, User
from guruji.db.store import Store
from guruji.geo.places import PlaceIndex
from guruji.geo.tz import birth_moment
from guruji.logs import user_tag
from guruji.rag.factors import current_factors, natal_factors
from guruji.rag.retrieve import Retriever
from guruji.safety.detect import SEVERITY, Category, Signal, detect
from guruji.safety.guard import violation
from guruji.safety.messages import HOLDING, REPLIES, SAFE_FALLBACK
from guruji.turn.reply import Reply
from guruji.turn.worker import Turn
from guruji.voice.speech import Speech, tts_language
from guruji.whatsapp.models import IncomingMessage

log = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
# Dossier maths is CPU-bound (~1 s); keep it off the event loop and bounded.
_CHART_SLOTS = asyncio.Semaphore(2)

Route = Literal["replay", "safety", "escalated", "onboarding", "guru", "silent"]
# While a human handles an escalation, remind the user at most this often.
HOLDING_EVERY = timedelta(hours=6)


class MediaSource(Protocol):
    async def download_media(self, media_id: str) -> tuple[bytes, str]: ...


class TurnState(TypedDict, total=False):
    turn: Turn
    user: User
    created: bool
    lang: Language
    draft: Draft
    reply: Reply
    write: TurnWrite
    first_reading: bool
    dossier: Dossier
    route: Route
    signal: Signal | None
    voice_in: bool  # the user spoke: answer with a voice note


class GuruResponder:
    """The `Responder` for the turn worker: runs the turn graph."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        sky: Sky,
        places: PlaceIndex | None,
        guru_models: list[BaseChatModel],
        extractor: BaseChatModel | None,
        retriever: Retriever | None = None,
        clock: Any = None,
        *,
        talk_models: list[BaseChatModel] | None = None,
        cache_blocks: bool = True,
        speech: Speech | None = None,
        media: MediaSource | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.sky = sky
        self.places = places
        self.extractor = extractor
        self.retriever = retriever
        self.speech = speech
        self.media = media
        self.cipher = FieldCipher(decode_key(settings.field_encryption_key))
        self.lookup_key = decode_key(settings.lookup_hmac_key)
        # Real work (readings) on the reading model; conversation on the talk model.
        # `extractor` (the fast model) also routes turns between them.
        self.guru = build_guru(guru_models, cache_blocks=cache_blocks)
        self.talk = build_guru(talk_models, cache_blocks=cache_blocks) if talk_models else None
        self.now = clock or (lambda: datetime.now(UTC))
        self.graph = self._build()

    def _build(self) -> Any:
        g: StateGraph[TurnState] = StateGraph(TurnState)
        g.add_node("load", self._load)
        g.add_node("replay", self._replay)
        g.add_node("safety", self._safety)
        g.add_node("escalated", self._escalated)
        g.add_node("onboarding", self._onboarding)
        g.add_node("cast_chart", self._cast_chart)
        g.add_node("guru", self._guru)
        g.add_node("silent", self._silent)
        g.add_node("commit", self._commit)
        g.add_edge(START, "load")
        g.add_conditional_edges("load", lambda s: s["route"])
        g.add_conditional_edges(
            "onboarding", lambda s: "cast_chart" if s.get("first_reading") else "commit"
        )
        g.add_conditional_edges(
            "cast_chart", lambda s: "guru" if s.get("first_reading") else "commit"
        )
        g.add_edge("guru", "commit")
        g.add_edge("safety", "commit")
        g.add_edge("escalated", "commit")
        g.add_edge("silent", "commit")
        g.add_edge("replay", END)
        g.add_edge("commit", END)
        return g.compile()

    async def respond(self, turn: Turn) -> Reply:
        out = await self.graph.ainvoke({"turn": turn})
        reply: Reply = out["reply"]
        return reply

    # --- nodes ---------------------------------------------------------------------

    async def _load(self, state: TurnState) -> TurnState:
        s = state
        turn = s["turn"]
        user, created = await self.store.get_or_create_user(
            lookup_hash(self.lookup_key, turn.wa_id)
        )
        if created:
            await self.store.set_wa_id(user.id, self.cipher.encrypt("wa_id", turn.wa_id, user.id))
        stored = await self.store.stored_reply(turn.turn_id)
        consented = user.state != "new"
        voice_in = False
        if stored is None and consented and any(m.kind == "audio" for m in turn.messages):
            messages, voice_in = await self._transcribe(turn)
            turn = Turn(turn.turn_id, turn.wa_id, messages)
        # Only what they say sets the language; button titles are in our words, not theirs.
        typed = "\n".join(m.text for m in turn.messages if m.kind == "text")
        lang = update_language(_as_language(user.language), typed)
        draft = self._draft(user)
        write = TurnWrite(
            user_id=user.id,
            turn_id=turn.turn_id,
            inbound=[
                # Nothing a user says is kept before they consent (DPDP).
                InboundLog(m.wamid, _logged_kind(m), m.text if consented else None)
                for m in turn.messages
            ],
            language=lang if lang != user.language else None,
            referral=next((m.referral for m in turn.messages if m.referral), None)
            if created
            else None,
        )
        # Safety signals are checked in every state, even before consent.
        signal = detect(typed) if typed else None
        route: Route
        if stored is not None:
            route = "replay"
        elif user.state == "escalated":
            route = "escalated"
        elif signal is not None:
            route = "safety"
        elif user.state == "active":
            route = "guru"
        elif user.state == "blocked" and draft.step != "declined":
            route = "silent"
        else:
            route = "onboarding"
        s_out: TurnState = {
            "turn": turn,
            "signal": signal,
            "voice_in": voice_in,
            "user": user,
            "created": created,
            "lang": lang,
            "draft": draft,
            "write": write,
            "route": route,
        }
        if stored is not None:
            s_out["reply"] = Reply.from_stored(stored.body, stored.meta)
        return s_out

    async def _replay(self, state: TurnState) -> TurnState:
        return {}

    async def _silent(self, state: TurnState) -> TurnState:
        return {"reply": Reply([])}

    async def _safety(self, state: TurnState) -> TurnState:
        signal = state.get("signal")
        assert signal is not None
        return self._open_escalation(state, signal.category)

    async def _escalated(self, state: TurnState) -> TurnState:
        s = state
        user, signal, lang = s["user"], s.get("signal"), s["lang"]
        esc = await self.store.active_escalation(user.id)
        if signal is not None and signal.severity == 1:
            # A fresh emergency while a human is on it: full scripted help, alert again.
            return {
                "reply": Reply(
                    list(REPLIES[signal.category][lang]),
                    kind="safety",
                    alert_escalation_id=esc.id if esc else None,
                )
            }
        if await self._held_recently(user.id):
            return {"reply": Reply([])}
        return {"reply": Reply([HOLDING[lang]], kind="holding")}

    def _open_escalation(self, state: TurnState, category: Category) -> TurnState:
        s = state
        user, w, lang = s["user"], s["write"], s["lang"]
        esc = EscalationOpen(str(uuid.uuid4()), category, SEVERITY[category], user.state)
        w.escalation = esc
        w.state = "escalated"
        log.warning(
            "escalation id=%s user=%s category=%s severity=%d",
            esc.id,
            user_tag(s["turn"].wa_id),
            category,
            esc.severity,
        )
        reply = Reply(list(REPLIES[category][lang]), kind="safety", alert_escalation_id=esc.id)
        return {"reply": reply}

    async def _held_recently(self, user_id: str) -> bool:
        cutoff = self.now() - HOLDING_EVERY
        for m in reversed(await self.store.recent_messages(user_id, 20)):
            if m.direction == "out" and (m.meta or {}).get("kind") == "holding":
                return m.created_at > cutoff
        return False

    async def _onboarding(self, state: TurnState) -> TurnState:
        s = state
        deps = OnboardingDeps(
            places=self.places,
            extractor=self.extractor,
            privacy_url=self.settings.privacy_notice_url,
            notice_version=self.settings.notice_version,
            today=self.now().astimezone(_IST).date(),
        )
        res = await Onboarding(deps).turn(
            s["user"].state, s["draft"], s["turn"].messages, s["lang"]
        )
        w = s["write"]
        w.state = res.state if res.state != s["user"].state else None
        w.consents.extend(res.consents)
        w.onboarding = self._seal_draft(s["user"].id, res.draft)
        if res.state != "new":  # consent given in this turn: keep what they typed
            w.inbound = [InboundLog(m.wamid, m.kind, m.text) for m in s["turn"].messages]
        return {"reply": res.reply, "draft": res.draft, "first_reading": res.complete}

    async def _cast_chart(self, state: TurnState) -> TurnState:
        s = state
        draft, user, w = s["draft"], s["user"], s["write"]
        assert draft.date and draft.place
        moment = birth_moment(
            date.fromisoformat(draft.date),
            time.fromisoformat(draft.time) if draft.time else None,
            draft.place.tz_name,
        )
        birth = BirthInput(
            moment=moment,
            latitude=draft.place.latitude,
            longitude=draft.place.longitude,
            time_known=not draft.time_unknown and draft.time is not None,
        )
        try:
            async with _CHART_SLOTS:
                dossier = await asyncio.to_thread(compute_dossier, self.sky, birth, self.now())
        except OutOfRangeError:
            draft.step = "fix"
            w.onboarding = self._seal_draft(user.id, draft)
            return {"reply": Reply([LINES["chart_error"][s["lang"]]]), "first_reading": False}
        w.chart = (ENGINE_VERSION, json.loads(dossier.model_dump_json()))
        w.birth = self._seal_birth(user.id, draft)
        w.state = "active"
        w.onboarding = {}
        return {"first_reading": True, "dossier": dossier}

    async def _guru(self, state: TurnState) -> TurnState:
        s = state
        user, turn, w = s["user"], s["turn"], s["write"]
        if not turn.text.strip() and not s.get("first_reading"):
            unclear = any(m.kind == "audio" for m in turn.messages) and self.speech is not None
            return {"reply": Reply([LINES["voice_unclear" if unclear else "text_only"][s["lang"]]])}
        dossier = s.get("dossier")
        if dossier is None:
            raw = await self.store.get_chart(user.id, ENGINE_VERSION)
            if raw is None:
                log.error("active user without chart user=%s", user_tag(turn.wa_id))
                return {"reply": Reply([LINES["didnt_get"][s["lang"]]])}
            dossier = Dossier.model_validate(raw)
        now = self.now()
        first = bool(s.get("first_reading"))
        spoken = bool(s.get("voice_in")) and self.settings.voice_enabled
        try:
            transits = await asyncio.to_thread(transit_snapshot, self.sky, dossier.d1, now)
        except OutOfRangeError:
            transits = None
        factors = natal_factors(dossier) | current_factors(dossier, now, transits)
        text = turn.text if turn.text.strip() else "(first reading)"
        history = await self._history(user.id)
        route: ModelRoute = "reading"
        if not first and (self.talk is not None or self.extractor is not None):
            previous = next(
                (m.content for m in reversed(history) if isinstance(m, AIMessage)), None
            )
            decision = await route_turn(
                self.extractor, turn.text, str(previous) if previous else None
            )
            if decision.safety != "none":
                return self._open_escalation(s, decision.safety)
            route = decision.route if self.talk is not None else "reading"
        log.info("guru turn user=%s route=%s", user_tag(turn.wa_id), route)
        cards = []
        if self.retriever is not None and route == "reading":
            if first:
                cards = await self.retriever.core(factors)
            else:
                cards = await self.retriever.for_question(turn.text, factors)
        ctx = GuruContext(
            sky=self.sky,
            dossier=dossier,
            now=now,
            name=await self._name(user, s.get("draft")),
            facts=await self.store.facts(user.id, 30),
            readings=await self.store.readings(user.id, 15),
            first_reading=first,
            # Spoken Hindi reads best from Devanagari, whatever script they would type in.
            language=("en" if s["lang"] == "en" else "hi") if spoken else s["lang"],
            spoken=spoken,
            transits=transits,
            cards=cards,
            factors=factors,
            retriever=self.retriever,
        )
        agent = self.talk if route == "talk" and self.talk is not None else self.guru
        bubbles = await run_guru(agent, history, text, ctx)
        broken = violation("\n".join(bubbles))
        if broken is not None:
            # The style check already asked for one rewrite; don't send it a second time.
            log.warning("guardrail fallback user=%s rule=%s", user_tag(turn.wa_id), broken)
            bubbles = [SAFE_FALLBACK[s["lang"]]]
            ctx.new_readings.clear()
        w.facts.extend(ctx.new_facts)
        w.readings.extend(ctx.new_readings)
        voice = tts_language(s["lang"]) if spoken and bubbles else None
        return {"reply": Reply(bubbles, voice_language=voice)}

    async def _commit(self, state: TurnState) -> TurnState:
        s = state
        w, reply = s["write"], s["reply"]
        w.reply_body = reply.body if reply.bubbles else None
        w.reply_meta = reply.to_meta() if reply.bubbles else None
        if not await self.store.commit_turn(w):
            stored = await self.store.stored_reply(w.turn_id)  # lost a race with a redelivery
            if stored is not None:
                return {"reply": Reply.from_stored(stored.body, stored.meta)}
        return {}

    # --- helpers ---------------------------------------------------------------------

    async def _transcribe(self, turn: Turn) -> tuple[list[IncomingMessage], bool]:
        """Voice notes become text messages. Failures stay audio (answered 'please type')."""
        if self.speech is None or self.media is None:
            return turn.messages, False
        out: list[IncomingMessage] = []
        heard = False
        for m in turn.messages:
            if m.kind != "audio" or not m.media_id:
                out.append(m)
                continue
            try:
                audio, mime = await self.media.download_media(m.media_id)
                transcript = await self.speech.transcribe(audio, mime)
            except Exception as e:  # WhatsApp or Sarvam trouble: ask them to type
                log.warning("voice note not transcribed: %s", type(e).__name__)
                out.append(m)
                continue
            if transcript.text:
                out.append(m.model_copy(update={"kind": "text", "text": transcript.text}))
                heard = True
            else:
                out.append(m)
        return out, heard

    async def _history(self, user_id: str) -> list[BaseMessage]:
        msgs = await self.store.recent_messages(user_id, self.settings.history_messages)
        out: list[BaseMessage] = []
        for m in msgs:
            if not m.body:
                continue
            out.append(HumanMessage(m.body) if m.direction == "in" else AIMessage(m.body))
        while out and not isinstance(out[0], HumanMessage):
            out.pop(0)  # the model expects the conversation to open with the user
        return out

    async def _name(self, user: User, draft: Draft | None) -> str | None:
        if draft is not None and draft.name:
            return draft.name
        birth = await self.store.get_birth(user.id)
        if birth is None or birth.name_enc is None:
            return None
        return self.cipher.decrypt("name", birth.name_enc, user.id)

    def _draft(self, user: User) -> Draft:
        token = user.onboarding.get("enc")
        if not token:
            return Draft()
        return Draft.from_json(json.loads(self.cipher.decrypt_text("onboarding", token, user.id)))

    def _seal_draft(self, user_id: str, draft: Draft) -> dict[str, Any]:
        plain = json.dumps(draft.to_json(), ensure_ascii=False)
        return {"enc": self.cipher.encrypt_text("onboarding", plain, user_id)}

    def _seal_birth(self, user_id: str, draft: Draft) -> EncryptedBirth:
        assert draft.date and draft.place
        enc = self.cipher.encrypt
        return EncryptedBirth(
            name_enc=enc("name", draft.name, user_id) if draft.name else None,
            date_enc=enc("birth_date", draft.date, user_id),
            time_enc=enc("birth_time", draft.time, user_id) if draft.time else None,
            time_known=draft.time is not None,
            place_enc=enc("place_label", draft.place.label, user_id),
            latitude_enc=enc("latitude", repr(draft.place.latitude), user_id),
            longitude_enc=enc("longitude", repr(draft.place.longitude), user_id),
            tz_name=draft.place.tz_name,
        )


def _logged_kind(m: IncomingMessage) -> str:
    """Transcribed voice notes are logged as audio, with the transcript as the body."""
    return "audio" if m.media_id and m.kind == "text" else m.kind


def _as_language(value: str | None) -> Language | None:
    return value if value in ("en", "hi", "hinglish") else None  # type: ignore[return-value]
