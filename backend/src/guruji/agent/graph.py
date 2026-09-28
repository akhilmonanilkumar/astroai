"""The turn graph (LangGraph): who is this, where are they, what do we say.

    load ─┬─ replay (turn already committed: resend the stored reply)
          ├─ safety (crisis / emergency / asks for a human: scripted reply, escalate) ─ commit
          ├─ escalated (a human is handling it: holding reply or silence) ─ commit
          ├─ onboarding ─┬─ cast_chart ─ guru (first reading) ─ commit
          │              └─ commit
          ├─ guru ─ commit          (may divert to safety on the model's second opinion)
          ├─ waitlist (closed beta: new user without an invite code) ─ commit
          └─ silent (blocked) ─ commit

`load` also transcribes voice notes (after consent), so everything downstream sees text.
Consent, onboarding and safety replies are code, not prompts. Everything a turn changes
is committed at the end in one idempotent write keyed by turn_id.
"""

import asyncio
import json
import logging
import re
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Protocol, TypedDict
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from guruji.agent.commands import detect_command
from guruji.agent.copy import LINES
from guruji.agent.credits_copy import MONEY, MONEY_BUTTONS, plural
from guruji.agent.guru import GuruContext, build_guru, run_guru, to_bubbles
from guruji.agent.language import Language
from guruji.agent.language import update as update_language
from guruji.agent.metering import Decision as Charge
from guruji.agent.metering import Meter, Rules, decide
from guruji.agent.onboarding import Draft, Onboarding, OnboardingDeps
from guruji.agent.privacy import PRIVACY, PRIVACY_BUTTONS, PrivacyCommand, detect_privacy
from guruji.agent.router import Route as ModelRoute
from guruji.agent.router import route_turn
from guruji.agent.verify import check_reply, strip_wrong
from guruji.appconfig import ConfigReader
from guruji.astro import (
    BirthInput,
    Dossier,
    OutOfRangeError,
    Sky,
    compute_dossier,
    transit_snapshot,
)
from guruji.astro.dossier import ENGINE_VERSION
from guruji.billing import catalog
from guruji.billing.razorpay import RazorpayError
from guruji.config import Settings
from guruji.crypto import FieldCipher, decode_key, lookup_hash
from guruji.db.models import (
    CreditWrite,
    EncryptedBirth,
    EscalationOpen,
    InboundLog,
    Order,
    TurnWrite,
    User,
)
from guruji.db.store import Store
from guruji.geo.places import PlaceIndex
from guruji.geo.tz import birth_moment
from guruji.logs import user_tag
from guruji.rag.factors import current_factors, natal_factors
from guruji.rag.retrieve import Retriever
from guruji.safety.detect import SEVERITY, Category, Signal, detect
from guruji.safety.guard import violation
from guruji.safety.messages import HOLDING, REPLIES, SAFE_FALLBACK
from guruji.turn.reply import Button, Reply
from guruji.turn.worker import Turn
from guruji.voice.speech import Speech, tts_language
from guruji.whatsapp.models import IncomingMessage

log = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
# Dossier maths is CPU-bound (~1 s); keep it off the event loop and bounded.
_CHART_SLOTS = asyncio.Semaphore(2)

Route = Literal[
    "replay",
    "safety",
    "privacy",
    "escalated",
    "onboarding",
    "guru",
    "account",
    "silent",
    "waitlist",
]
_WORDS = re.compile(r"[A-Z0-9][A-Z0-9-]*")  # invite-code candidates in upper-cased text
_THUMBS_DOWN = "\U0001f44e"
_LIKED = ("\U0001f44d", "❤", "\U0001f64f", "\U0001f60d")  # 👍 ❤ 🙏 😍: a liked answer
# While a human handles an escalation, remind the user at most this often, and not at all
# within this long of the team's last message.
HOLDING_EVERY = timedelta(hours=6)


class MediaSource(Protocol):
    async def download_media(self, media_id: str) -> tuple[bytes, str]: ...


class PaymentLinks(Protocol):
    """Razorpay payment links (PAYMENT_CHECKOUT=link); idempotent per reference_id."""

    async def create_link(
        self, reference_id: str, amount_paise: int, description: str, expire_by: int
    ) -> str: ...


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
    privacy: PrivacyCommand | Literal["erase_yes", "erase_no"] | None


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
        links: PaymentLinks | None = None,
    ) -> None:
        self.settings = settings
        self.links = links
        self.store = store
        self.sky = sky
        self.places = places
        self.extractor = extractor
        self.retriever = retriever
        self.speech = speech
        self.media = media
        self.config = ConfigReader(store)
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
        g.add_node("account", self._account)
        g.add_node("privacy", self._privacy)
        g.add_node("silent", self._silent)
        g.add_node("waitlist", self._waitlist)
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
        g.add_edge("account", "commit")
        g.add_edge("privacy", "commit")
        g.add_edge("silent", "commit")
        g.add_edge("waitlist", "commit")
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
        heard_only = ""  # before consent: a transcript used for safety detection, then dropped
        if stored is None and any(m.kind == "audio" for m in turn.messages):
            messages, heard = await self._transcribe(turn)
            if consented:
                turn, voice_in = Turn(turn.turn_id, turn.wa_id, messages), heard
            else:
                # Help comes first, even before consent: listen for a crisis, keep nothing.
                heard_only = "\n".join(m.text for m in messages if m.kind == "text" and m.media_id)
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
        if signal is None and heard_only:
            signal = detect(heard_only)
            if signal is not None:  # answer the crisis in the language they spoke
                lang = update_language(lang, heard_only)
        # DPDP commands work in every state, right after safety (help comes first).
        privacy = _privacy_of(typed, turn.messages, user.state)
        route: Route
        if stored is not None:
            route = "replay"
        elif signal is not None and user.state != "escalated":
            route = "safety"
        elif privacy is not None:
            route = "privacy"
        elif user.state == "opted_out":
            route = "silent"  # after STOP, nothing but the privacy commands gets a reply
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
        # Reactions (a heart, a thumbs-down) are not questions: only a thumbs-down on an
        # answer does anything. Commands and "not now" are answered by code.
        spoken_msgs = [m for m in turn.messages if m.kind != "reaction"]
        if route not in ("replay", "safety"):
            if not spoken_msgs:
                route = "account" if user.state == "active" else "silent"
            elif route == "guru" and (
                detect_command(typed)
                or any(
                    m.reply_id == "spend_no" or (m.reply_id or "").startswith(catalog.BUY_PREFIX)
                    for m in spoken_msgs
                )
            ):
                route = "account"
            if spoken_msgs and len(spoken_msgs) < len(turn.messages) and route != "account":
                turn = Turn(turn.turn_id, turn.wa_id, spoken_msgs)
        # Closed beta: a new user needs an invite code before onboarding (after safety and
        # the privacy commands, which always work). Consented users are never affected.
        if route == "onboarding" and user.state == "new" and not user.admitted:
            admit, code = await self._admission(typed)
            if not admit:
                route = "waitlist"
            elif code is not None:
                write.admitted_by = code
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
            "privacy": privacy,
        }
        if stored is not None:
            s_out["reply"] = Reply.from_stored(stored.body, stored.meta)
        return s_out

    async def _admission(self, text: str) -> tuple[bool, str | None]:
        """(let them in?, the invite code they sent). Codes match whole words, any case."""
        beta = await self.config.get("beta")
        flags = await self.config.flags()
        wanted = {c.upper(): c for c in beta.codes}
        sent = next((wanted[w] for w in _WORDS.findall(text.upper()) if w in wanted), None)
        gated = beta.invite_only or not flags.new_user_admission
        return (sent is not None or not gated), sent

    async def _waitlist(self, state: TurnState) -> TurnState:
        """Not let in yet: a scripted reply; nothing they typed is kept (no consent)."""
        return {"reply": Reply([LINES["waitlist"][state["lang"]]], kind="waitlist")}

    async def _privacy(self, state: TurnState) -> TurnState:
        s = state
        user, w, lang, cmd = s["user"], s["write"], s["lang"], s.get("privacy")
        if cmd == "stop":
            w.state, w.opted_out = "opted_out", True
            log.info("opted out user=%s", user_tag(s["turn"].wa_id))
            return {"reply": Reply([PRIVACY["stopped"][lang]], kind="privacy")}
        if cmd == "start":
            onboarded = await self.store.get_chart(user.id, ENGINE_VERSION) is not None
            w.state, w.opted_out = ("active" if onboarded else "new"), False
            return {"reply": Reply([PRIVACY["started"][lang]], kind="privacy")}
        if cmd == "delete":
            buttons = [Button(i, PRIVACY_BUTTONS[i][lang]) for i in ("erase_yes", "erase_no")]
            return {"reply": Reply([PRIVACY["confirm_delete"][lang]], buttons, kind="privacy")}
        if cmd == "erase_yes":
            log.info("erasing user=%s on request", user_tag(s["turn"].wa_id))
            w.erase = True  # in the commit, with this turn's own messages
            return {"reply": Reply([PRIVACY["deleted"][lang]], kind="privacy", erased=True)}
        if cmd == "erase_no":
            return {"reply": Reply([PRIVACY["kept"][lang]], kind="privacy")}
        # export: built and sent as a file by the jobs role
        task = {"kind": "export", "user_id": user.id, "lang": lang}
        return {"reply": Reply([PRIVACY["export_soon"][lang]], kind="privacy", tasks=(task,))}

    async def _account(self, state: TurnState) -> TurnState:
        """Balance, a thumbs-down on an answer (refund), or "not now" to a cost."""
        s = state
        user, turn, w, lang = s["user"], s["turn"], s["write"], s["lang"]
        if any(m.reply_id == "spend_no" for m in turn.messages):
            meter = Meter.load(user.meter)
            if meter.pending:
                w.meter = replace(meter, pending=None).dump()
            return {"reply": Reply([MONEY["declined"][lang]])}
        downs = [
            m.reply_id
            for m in turn.messages
            if m.kind == "reaction" and m.reply_id and m.text.startswith(_THUMBS_DOWN)
        ]
        if downs:
            w.feedback = (downs[-1], "down")  # for persona tuning, paid answer or free
            return {"reply": await self._refund(user, turn.wa_id, downs[-1], w, lang)}
        ups = [
            m.reply_id
            for m in turn.messages
            if m.kind == "reaction" and m.reply_id and m.text.startswith(_LIKED)
        ]
        if ups:
            w.feedback = (ups[-1], "up")
        bought = next(
            (
                m.reply_id
                for m in turn.messages
                if (m.reply_id or "").startswith(catalog.BUY_PREFIX)
            ),
            None,
        )
        if bought:
            item_id = bought.removeprefix(catalog.BUY_PREFIX)
            return {"reply": await self._checkout(user, turn.turn_id, item_id, lang)}
        command = detect_command("\n".join(m.text for m in turn.messages if m.kind == "text"))
        if command == "balance":
            return {"reply": Reply([await self._balance_text(user, lang)])}
        if command == "topup":
            return {"reply": await self._offer(user, lang)}
        return {"reply": Reply([])}

    async def _offer(self, user: User, lang: Language, lead: str | None = None) -> Reply:
        """Packs and passes as a WhatsApp list; the trial pack only before a first buy."""
        plus = await self.config.get("plus_limits")
        card = catalog.offer(
            await self.config.get("packs"),
            await self.config.get("passes"),
            plus.prashnas_per_day,
            lang,
            first_buy=not await self.store.has_paid(user.id),
            by_link=self.settings.payment_checkout == "link",
        )
        if lead:
            card["body"]["text"] = f"{lead}\n\n{card['body']['text']}"
        return Reply([card["body"]["text"]], interactive=card, kind="offer")

    async def _checkout(self, user: User, turn_id: str, item_id: str, lang: Language) -> Reply:
        """Create the order and send WhatsApp's "Review and pay" card for it."""
        packs = await self.config.get("packs")
        items = catalog.items(packs, await self.config.get("passes"))
        item = items.get(item_id)
        trial = {p.id for p in packs if p.show_once}
        if item is None or (item_id in trial and await self.store.has_paid(user.id)):
            return await self._offer(user, lang)  # stale or no longer offered: show again
        # One order per turn: a redelivered turn finds the same order.
        ref = f"gj{turn_id.removeprefix('t_')}"[:35]
        await self.store.create_order(
            Order(
                ref,
                user.id,
                item.kind,
                item.id,
                item.price_inr * 100,
                prashnas=item.prashnas,
                days=item.days,
            )
        )
        hours = (await self.config.get("retention")).pending_order_hours
        expire_by = int((self.now() + timedelta(hours=hours)).timestamp())
        if self.settings.payment_checkout == "link":
            return await self._link_checkout(item, ref, lang, expire_by)
        card = catalog.checkout(item, ref, self.settings.wa_payment_config, lang, expire_by)
        log.info("checkout order=%s item=%s", ref, item.id)
        return Reply([card["body"]["text"]], interactive=card, kind="checkout")

    async def _link_checkout(
        self, item: catalog.Item, ref: str, lang: Language, expire_by: int
    ) -> Reply:
        """A Razorpay payment link (no WhatsApp payment configuration needed). Creating it
        is idempotent per reference_id, so a redelivered turn sends the same link."""
        if self.links is None:
            raise RuntimeError("PAYMENT_CHECKOUT=link needs Razorpay payment links")
        try:
            url = await self.links.create_link(
                ref, item.price_inr * 100, catalog.item_name(item, lang), expire_by
            )
        except RazorpayError as e:
            log.warning("payment link failed order=%s: %s", ref, e)
            return Reply([catalog.text("unavailable", lang)])
        card = catalog.link_checkout(item, url, lang)
        log.info("checkout link order=%s item=%s", ref, item.id)
        return Reply([card["body"]["text"]], interactive=card, kind="checkout", payment_check=ref)

    async def _refund(
        self, user: User, wa_id: str, answer_turn: str, w: TurnWrite, lang: Language
    ) -> Reply:
        spent = await self.store.credit_by_key(f"spend:{answer_turn}")
        if spent is None or spent[0] != user.id:
            return Reply([MONEY["sorry"][lang]])
        if await self.store.credit_by_key(f"refund:{answer_turn}") is not None:
            return Reply([])  # already refunded: don't apologise twice
        cost = -spent[1]
        w.credits.append(
            CreditWrite(cost, "refund", f"refund:{answer_turn}", {"turn_id": answer_turn})
        )
        log.info("thumbs-down refund user=%s credits=%d", user_tag(wa_id), cost)
        return Reply([MONEY["refunded"][lang].format(cost=cost, s=plural(cost, lang))])

    async def _balance_text(self, user: User, lang: Language) -> str:
        now = self.now()
        rules = await self._rules()
        balance = await self.store.balance(user.id)
        plan = await self.store.active_pass(user.id, now)
        meter = Meter.load(user.meter).on(now)
        plus = (
            MONEY["balance_plus"][lang].format(until=f"{plan.ends_at.astimezone(_IST):%d %b %Y}")
            if plan
            else ""
        )
        in_welcome = now - user.created_at < timedelta(hours=rules.free.welcome_hours)
        if in_welcome:
            n = max(0, rules.free.welcome_prashnas - meter.welcome_used)
            free = MONEY["balance_welcome"][lang].format(n=n, s=plural(n, lang))
        else:
            left = meter.day_free_used < rules.free.daily_free_answers
            state = MONEY["daily_left" if left else "daily_used"][lang]
            free = MONEY["balance_daily"][lang].format(state=state)
        return MONEY["balance"][lang].format(
            balance=balance, s=plural(balance, lang), plus=plus, free=free
        )

    async def _rules(self) -> Rules:
        return Rules(
            await self.config.get("free_tier"),
            await self.config.get("prashna"),
            await self.config.get("plus_limits"),
        )

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
            if m.direction == "out" and (m.meta or {}).get("kind") in ("holding", "human"):
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
        spoken = (
            bool(s.get("voice_in"))
            and self.settings.voice_enabled
            and (await self.config.flags()).voice_enabled
        )
        try:
            transits = await asyncio.to_thread(transit_snapshot, self.sky, dossier.d1, now)
        except OutOfRangeError:
            transits = None
        factors = natal_factors(dossier) | current_factors(dossier, now, transits)
        text = turn.text if turn.text.strip() else "(first reading)"
        history = await self._history(user.id)
        route: ModelRoute = "reading"
        meter = Meter.load(user.meter)
        # "Yes, go ahead" to a stated cost: answer the question that was waiting for it.
        accepted = bool(meter.pending) and any(m.reply_id == "spend_yes" for m in turn.messages)
        if accepted:
            assert meter.pending is not None
            text = str(meter.pending.get("text", ""))
            spoken = spoken and bool(meter.pending.get("voice"))
        elif not first and (self.talk is not None or self.extractor is not None):
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
        charge: Charge | None = None
        if route == "reading" and not first:
            charge = decide(
                meter,
                await self._rules(),
                now=now,
                joined=user.created_at,
                turn_id=turn.turn_id,
                balance=await self.store.balance(user.id),
                plus_active=await self.store.active_pass(user.id, now) is not None,
                voice=spoken,
                accepted=accepted,
            )
            if not charge.answers:
                asked = self._cost_reply(charge, text, spoken, now, w, s["lang"])
                if asked is None:
                    asked = await self._offer(user, s["lang"], MONEY["empty"][s["lang"]])
                return {"reply": asked}
            spoken = charge.voice
        cards = []
        if self.retriever is not None and route == "reading":
            if first:
                cards = await self.retriever.core(factors)
            else:
                cards = await self.retriever.for_question(text, factors)
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
            brief=bool(charge and charge.brief),
            transits=transits,
            cards=cards,
            factors=factors,
            retriever=self.retriever,
        )
        agent = self.talk if route == "talk" and self.talk is not None else self.guru
        try:
            bubbles = await asyncio.wait_for(
                run_guru(agent, history, text, ctx), self.settings.guru_budget_seconds
            )
        except TimeoutError:
            # Out of time: a scripted reply now beats a late one. Not charged, nothing kept.
            log.warning("guru over budget user=%s route=%s", user_tag(turn.wa_id), route)
            return {"reply": Reply([LINES["slow"][s["lang"]]], kind="slow")}
        broken = violation("\n".join(bubbles))
        if broken is not None:
            # The style check already asked for one rewrite; don't send it a second time.
            log.warning("guardrail fallback user=%s rule=%s", user_tag(turn.wa_id), broken)
            bubbles = [SAFE_FALLBACK[s["lang"]]]
            ctx.new_readings.clear()
        bubbles = self._drop_wrong_facts(bubbles, ctx, s["lang"], turn.wa_id)
        w.facts.extend(ctx.new_facts)
        w.readings.extend(ctx.new_readings)
        answered = bool(bubbles) and bubbles != [SAFE_FALLBACK[s["lang"]]]
        if charge is not None and answered:
            # Paid for only when the answer came through; a failed answer costs nothing.
            w.meter = charge.meter.dump()
            if charge.cost:
                w.credits.append(
                    CreditWrite(
                        -charge.cost,
                        "spend",
                        f"spend:{turn.turn_id}",
                        {"prashna": charge.meter.prashna_id},
                    )
                )
                if not spoken:  # a voice note would read the note aloud
                    left = charge.balance - charge.cost
                    note = MONEY["used"][s["lang"]].format(cost=charge.cost, left=left)
                    bubbles = [*bubbles[:-1], f"{bubbles[-1]}\n\n{note}"]
        voice = tts_language(s["lang"]) if spoken and bubbles else None
        kind = "first_reading" if first else "reply"  # the turn worker reports a Lead for it
        return {"reply": Reply(bubbles, kind=kind, voice_language=voice)}

    def _cost_reply(
        self, charge: Charge, text: str, spoken: bool, now: datetime, w: TurnWrite, lang: Language
    ) -> Reply | None:
        """The cost question, or None when nothing is left (the caller offers a top-up)."""
        """Ask before spending (the question waits in the meter), or say nothing is left."""
        if charge.kind == "confirm":
            pending = {"text": text[:2000], "voice": spoken, "at": now.isoformat()}
            w.meter = replace(charge.meter, pending=pending).dump()
            line = MONEY["confirm"][lang].format(
                cost=charge.cost, s=plural(charge.cost, lang), balance=charge.balance
            )
            buttons = [Button(i, MONEY_BUTTONS[i][lang]) for i in ("spend_yes", "spend_no")]
            return Reply([line], buttons, kind="cost")
        w.meter = charge.meter.dump()
        return None

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

    def _drop_wrong_facts(
        self, bubbles: list[str], ctx: GuruContext, lang: Language, wa_id: str
    ) -> list[str]:
        """After the model's rewrites, never send a chart fact that is still wrong: drop
        those sentences (and this turn's readings, which may carry the same error)."""
        text = "\n\n".join(bubbles)
        problems = check_reply(text, ctx.dossier, ctx.now, sky=self.sky, transits=ctx.transits)
        if ctx.fact_rewrites or problems:
            log.info(
                "fact check user=%s rewrites=%d unresolved=%d",
                user_tag(wa_id),
                ctx.fact_rewrites,
                len(problems),
            )
        if not problems:
            return bubbles
        ctx.new_readings.clear()
        kept = strip_wrong(text, problems)
        return to_bubbles(kept) if kept.strip() else [SAFE_FALLBACK[lang]]

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


def _privacy_of(
    typed: str, messages: list[IncomingMessage], state: str
) -> PrivacyCommand | Literal["erase_yes", "erase_no"] | None:
    for m in messages:
        if m.reply_id in ("erase_yes", "erase_no"):
            return m.reply_id  # type: ignore[return-value]
    cmd = detect_privacy(typed) if typed else None
    if cmd == "start" and state != "opted_out":
        return None  # "start" from anyone else is just a greeting
    return cmd


def _logged_kind(m: IncomingMessage) -> str:
    """Transcribed voice notes are logged as audio, with the transcript as the body."""
    return "audio" if m.media_id and m.kind == "text" else m.kind


def _as_language(value: str | None) -> Language | None:
    return value if value in ("en", "hi", "hinglish") else None  # type: ignore[return-value]
