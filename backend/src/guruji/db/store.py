"""The `Store` protocol and an in-process implementation for dev and tests.

The turn worker reads through the store and writes one `TurnWrite` per turn. Commits
are idempotent by turn_id, so a redelivered turn replays the stored reply instead of
running the guru (and its side effects) twice.
"""

import statistics
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from guruji.appconfig import DEFAULTS as CONFIG_DEFAULTS
from guruji.db.admin import (
    AdminMessage,
    AdminRole,
    AdminStore,
    AdminUser,
    AdStats,
    AuditEntry,
    ConfigEntry,
    CreditReason,
    DayStats,
    EscalationRow,
    FeedbackRow,
    LedgerEntry,
    Metrics,
    UserRecord,
    UserRow,
)
from guruji.db.models import (
    Consent,
    EncryptedBirth,
    Escalation,
    LifeFact,
    LoggedMessage,
    Order,
    Pass,
    Rating,
    Reading,
    StoredReply,
    TurnWrite,
    User,
    UserState,
)


class Store(AdminStore, Protocol):
    async def get_or_create_user(self, wa_hash: str) -> tuple[User, bool]: ...
    async def set_wa_id(self, user_id: str, wa_id_enc: bytes) -> None: ...
    async def get_birth(self, user_id: str) -> EncryptedBirth | None: ...
    async def get_chart(self, user_id: str, engine_version: str) -> dict[str, Any] | None: ...
    async def recent_messages(self, user_id: str, limit: int) -> list[LoggedMessage]: ...
    async def facts(self, user_id: str, limit: int) -> list[LifeFact]: ...
    async def readings(self, user_id: str, limit: int) -> list[Reading]: ...
    async def stored_reply(self, turn_id: str) -> StoredReply | None: ...
    async def balance(self, user_id: str) -> int: ...
    async def active_pass(self, user_id: str, now: datetime) -> Pass | None: ...
    async def credit_by_key(self, key: str) -> tuple[str, int] | None:
        """(user_id, delta) of the ledger row with this idempotency key, if any."""
        ...

    async def add_pass(self, user_id: str, plan_id: str, days: int, source: str) -> Pass | None:
        """Start (or extend from the current end) a pass; None if `source` was used."""
        ...

    async def ctwa_clid(self, user_id: str) -> str | None:
        """The click id of the Click-to-WhatsApp ad that brought this user, if any."""
        ...

    # --- DPDP -------------------------------------------------------------------------
    async def erase_user(self, user_id: str) -> bool:
        """Erase everything personal: conversations, birth details, chart, memory, ad data,
        passes, escalations, the phone number. The user row stays, anonymised, for the
        payment records (credit_ledger, orders) and the consent proof (no personal data).
        False if already erased."""
        ...

    async def retention_sweep(
        self, now: datetime, *, opted_out_days: int, message_days: int, pending_order_hours: int
    ) -> dict[str, int]:
        """Erase users opted out long enough, drop old conversation text, expire orders."""
        ...

    # --- payments ---------------------------------------------------------------------
    async def create_order(self, order: Order) -> None: ...
    async def get_order(self, reference_id: str) -> Order | None: ...
    async def has_paid(self, user_id: str) -> bool: ...
    async def fulfil_order(self, reference_id: str, payment_id: str) -> Order | None:
        """Mark paid and credit it (credits or a pass) in one step. Returns the order the
        first time; None if already paid or unknown. Razorpay is the source of truth, so a
        payment that lands after we marked the order failed or expired is still credited."""
        ...

    async def fail_order(self, reference_id: str) -> bool: ...
    async def orders_to_reconcile(self, since: datetime, limit: int) -> list[str]:
        """Unpaid orders (pending, failed or expired) created after `since`, oldest first:
        re-checked with Razorpay in case a payment arrived without a webhook."""
        ...

    async def commit_turn(self, w: TurnWrite) -> bool:
        """Apply the turn's writes; False (and no change) if turn_id was already committed."""
        ...

    # --- escalations ------------------------------------------------------------------
    async def active_escalation(self, user_id: str) -> Escalation | None: ...
    async def get_escalation(self, escalation_id: str) -> Escalation | None: ...
    async def acknowledge_escalation(self, escalation_id: str, by: str) -> bool:
        """Mark acknowledged; False if it was not open."""
        ...

    async def mark_alerted(self, escalation_id: str) -> None: ...
    async def escalations_to_reping(self, stale_before: datetime) -> list[Escalation]:
        """Open (unacknowledged) severity-1 escalations last alerted before the cutoff."""
        ...

    async def resolve_escalation(self, escalation_id: str, hand_back: bool) -> bool:
        """Close it and return the user to the guru; False if it was not active."""
        ...

    async def aclose(self) -> None: ...


def _now() -> datetime:
    return datetime.now(UTC)


_IST = ZoneInfo("Asia/Kolkata")


def ist_day(t: datetime) -> date:
    return t.astimezone(_IST).date()


class MemoryStore:
    """Dict-backed store. Single process only (the `dev` role and tests)."""

    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.by_hash: dict[str, str] = {}
        self._last_created = datetime.min.replace(tzinfo=UTC)
        self.wa_ids: dict[str, bytes] = {}
        self.births: dict[str, EncryptedBirth] = {}
        self.charts: dict[tuple[str, str], dict[str, Any]] = {}
        self.messages: dict[str, list[AdminMessage]] = defaultdict(list)
        self.inbound_wamids: set[str] = set()
        self.replies: dict[str, StoredReply] = {}
        self.consents: dict[str, list[Consent]] = defaultdict(list)
        self.life_facts: dict[str, list[LifeFact]] = defaultdict(list)
        self.reading_log: dict[str, list[Reading]] = defaultdict(list)
        self.referrals: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.escalations: dict[str, Escalation] = {}
        self._prior_state: dict[str, UserState] = {}
        # admin console
        self._message_seq = 0
        self.human_turns: set[str] = set()
        self.chart_at: dict[str, datetime] = {}
        self.closed: dict[str, tuple[datetime, str, str | None]] = {}  # at, by, note
        self.ledger: dict[str, list[LedgerEntry]] = defaultdict(list)
        self.ledger_keys: dict[str, tuple[str, int]] = {}
        self.passes: dict[str, list[Pass]] = defaultdict(list)
        self.orders: dict[str, Order] = {}
        self.opted_out_at: dict[str, datetime] = {}
        self.invite_codes: dict[str, str] = {}  # user id -> the code that admitted them
        # feedback: (user id, answer turn) -> (id, rating, at); and each turn's texts
        self.feedback: dict[tuple[str, str], tuple[int, Rating, datetime]] = {}
        self._feedback_seq = 0
        self.turn_texts: dict[tuple[str, str], tuple[str | None, str | None]] = {}
        self.erased: set[str] = set()
        self.pass_sources: set[str] = set()
        self.config: dict[str, ConfigEntry] = {
            k: ConfigEntry(k, v, _now(), "migration") for k, v in CONFIG_DEFAULTS.items()
        }
        self.audit_log: list[AuditEntry] = []
        self.admins: dict[str, AdminUser] = {}

    def _log(
        self,
        user_id: str,
        direction: Any,
        sent_by: Any,
        kind: str,
        body: str | None,
        at: datetime,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self._message_seq += 1
        self.messages[user_id].append(
            AdminMessage(self._message_seq, direction, sent_by, kind, body, at, meta)
        )

    async def get_or_create_user(self, wa_hash: str) -> tuple[User, bool]:
        if wa_hash in self.by_hash:
            return replace(self.users[self.by_hash[wa_hash]]), False
        # Distinct creation times, as in Postgres: Windows' clock ticks every ~15 ms, and
        # equal times would break newest-first order and the `before` paging cursor.
        created = max(_now(), self._last_created + timedelta(microseconds=1))
        self._last_created = created
        user = User(id=str(uuid4()), state="new", language=None, onboarding={}, created_at=created)
        self.users[user.id] = user
        self.by_hash[wa_hash] = user.id
        return replace(user), True

    async def set_wa_id(self, user_id: str, wa_id_enc: bytes) -> None:
        self.wa_ids[user_id] = wa_id_enc

    async def get_birth(self, user_id: str) -> EncryptedBirth | None:
        return self.births.get(user_id)

    async def get_chart(self, user_id: str, engine_version: str) -> dict[str, Any] | None:
        return self.charts.get((user_id, engine_version))

    async def recent_messages(self, user_id: str, limit: int) -> list[LoggedMessage]:
        logged = [m for m in self.messages[user_id] if m.body is not None][-limit:]
        return [
            LoggedMessage(m.direction, m.sent_by, m.kind, m.body, m.created_at, m.meta)
            for m in logged
        ]

    async def facts(self, user_id: str, limit: int) -> list[LifeFact]:
        return self.life_facts[user_id][-limit:]

    async def readings(self, user_id: str, limit: int) -> list[Reading]:
        return self.reading_log[user_id][-limit:]

    async def stored_reply(self, turn_id: str) -> StoredReply | None:
        return self.replies.get(turn_id)

    async def balance(self, user_id: str) -> int:
        return self._balance(user_id)

    async def active_pass(self, user_id: str, now: datetime) -> Pass | None:
        live = [p for p in self.passes[user_id] if p.starts_at <= now < p.ends_at]
        return max(live, key=lambda p: p.ends_at, default=None)

    async def credit_by_key(self, key: str) -> tuple[str, int] | None:
        return self.ledger_keys.get(key)

    async def erase_user(self, user_id: str) -> bool:
        if user_id in self.erased or user_id not in self.users:
            return False
        self.erased.add(user_id)
        for table in (
            self.messages,
            self.life_facts,
            self.reading_log,
            self.referrals,
            self.passes,
        ):
            table.pop(user_id, None)
        self.births.pop(user_id, None)
        self.wa_ids.pop(user_id, None)
        for fkey in [k for k in self.feedback if k[0] == user_id]:
            del self.feedback[fkey]
        for tkey in [k for k in self.turn_texts if k[0] == user_id]:
            del self.turn_texts[tkey]
        for key in [k for k in self.charts if k[0] == user_id]:
            del self.charts[key]
        for esc_id in [e.id for e in self.escalations.values() if e.user_id == user_id]:
            del self.escalations[esc_id]
        for h in [h for h, uid in self.by_hash.items() if uid == user_id]:
            del self.by_hash[h]
        self.opted_out_at.pop(user_id, None)
        u = self.users[user_id]
        u.state, u.language, u.onboarding, u.meter = "new", None, {}, {}
        return True

    async def retention_sweep(
        self, now: datetime, *, opted_out_days: int, message_days: int, pending_order_hours: int
    ) -> dict[str, int]:
        due = [
            uid
            for uid, at in self.opted_out_at.items()
            if at < now - timedelta(days=opted_out_days)
        ]
        erased = sum([await self.erase_user(uid) for uid in due])
        cutoff = now - timedelta(days=message_days)
        dropped = 0
        for uid, msgs in self.messages.items():
            keep = [m for m in msgs if m.created_at >= cutoff]
            dropped += len(msgs) - len(keep)
            self.messages[uid] = keep
        expired = 0
        for ref, o in list(self.orders.items()):
            stale = o.created_at and o.created_at < now - timedelta(hours=pending_order_hours)
            if o.status == "pending" and stale:
                self.orders[ref] = replace(o, status="expired")
                expired += 1
        return {"users_erased": erased, "messages_dropped": dropped, "orders_expired": expired}

    async def ctwa_clid(self, user_id: str) -> str | None:
        refs = [r for r in self.referrals[user_id] if r.get("ctwa_clid")]
        return str(refs[-1]["ctwa_clid"]) if refs else None

    async def create_order(self, order: Order) -> None:
        self.orders.setdefault(order.reference_id, replace(order, created_at=_now()))

    async def get_order(self, reference_id: str) -> Order | None:
        return self.orders.get(reference_id)

    async def has_paid(self, user_id: str) -> bool:
        return any(o.user_id == user_id and o.status == "paid" for o in self.orders.values())

    async def fulfil_order(self, reference_id: str, payment_id: str) -> Order | None:
        o = self.orders.get(reference_id)
        if o is None or o.status == "paid":
            return None
        if o.kind == "pack":
            assert o.prashnas
            await self.add_credits(
                o.user_id,
                o.prashnas,
                "purchase",
                f"purchase:{reference_id}",
                {"order": reference_id, "payment_id": payment_id},
            )
        else:
            assert o.days
            await self.add_pass(o.user_id, o.item_id, o.days, f"payment:{reference_id}")
        paid = replace(o, status="paid", payment_id=payment_id)
        self.orders[reference_id] = paid
        return paid

    async def fail_order(self, reference_id: str) -> bool:
        o = self.orders.get(reference_id)
        if o is None or o.status != "pending":
            return False
        self.orders[reference_id] = replace(o, status="failed")
        return True

    async def orders_to_reconcile(self, since: datetime, limit: int) -> list[str]:
        unpaid = [
            o
            for o in self.orders.values()
            if o.status != "paid" and o.created_at is not None and o.created_at > since
        ]
        unpaid.sort(key=lambda o: o.created_at or since)
        return [o.reference_id for o in unpaid[:limit]]

    async def add_pass(self, user_id: str, plan_id: str, days: int, source: str) -> Pass | None:
        if source in self.pass_sources:
            return None
        self.pass_sources.add(source)
        now = _now()
        start = max([now, *(p.ends_at for p in self.passes[user_id])])
        p = Pass(plan_id, start, start + timedelta(days=days))
        self.passes[user_id].append(p)
        return p

    async def commit_turn(self, w: TurnWrite) -> bool:
        if w.turn_id in self.replies:
            return False
        now = _now()
        for m in w.inbound:
            if m.wamid not in self.inbound_wamids:
                self.inbound_wamids.add(m.wamid)
                self._log(w.user_id, "in", "user", m.kind, m.body, now)
        user = self.users[w.user_id]
        if w.state is not None:
            user.state = w.state
        if w.language is not None:
            user.language = w.language
        if w.onboarding is not None:
            user.onboarding = dict(w.onboarding)
        if w.meter is not None:
            user.meter = dict(w.meter)
        if w.opted_out is True:
            self.opted_out_at[w.user_id] = now
        elif w.opted_out is False:
            self.opted_out_at.pop(w.user_id, None)
        if w.admitted_by is not None and not user.admitted:
            user.admitted = True
            self.invite_codes[w.user_id] = w.admitted_by
        spoken = [m.body for m in w.inbound if m.kind != "reaction" and m.body is not None]
        self.turn_texts[(w.user_id, w.turn_id)] = ("\n".join(spoken) or None, w.reply_body)
        if w.feedback is not None:
            self._feedback_seq += 1
            turn, rating = w.feedback
            self.feedback[(w.user_id, turn)] = (self._feedback_seq, rating, now)
        for c in w.credits:
            await self.add_credits(w.user_id, c.delta, c.reason, c.key, c.ref)  # type: ignore[arg-type]
        self.consents[w.user_id].extend(w.consents)
        if w.birth is not None:
            self.births[w.user_id] = w.birth
        if w.chart is not None:
            self.charts[(w.user_id, w.chart[0])] = w.chart[1]
            self.chart_at.setdefault(w.user_id, now)
        self.life_facts[w.user_id].extend(LifeFact(c, f, now) for c, f in w.facts)
        self.reading_log[w.user_id].extend(Reading(t, s, list(f), now) for t, s, f in w.readings)
        if w.referral is not None:
            self.referrals[w.user_id].append(w.referral)
        if w.escalation is not None and await self.active_escalation(w.user_id) is None:
            e = w.escalation
            self.escalations[e.id] = Escalation(
                e.id, w.user_id, e.category, e.severity, "open", 0, now
            )
            self._prior_state[e.id] = e.prior_state
        body = w.reply_body or ""
        self.replies[w.turn_id] = StoredReply(body, w.reply_meta)
        if w.reply_body is not None:
            self._log(w.user_id, "out", "guru", "text", w.reply_body, now, w.reply_meta)
        if w.erase:
            await self.erase_user(w.user_id)
        return True

    async def active_escalation(self, user_id: str) -> Escalation | None:
        return next(
            (
                replace(e)
                for e in self.escalations.values()
                if e.user_id == user_id and e.status in ("open", "acknowledged")
            ),
            None,
        )

    async def get_escalation(self, escalation_id: str) -> Escalation | None:
        e = self.escalations.get(escalation_id)
        return replace(e) if e else None

    async def acknowledge_escalation(self, escalation_id: str, by: str) -> bool:
        e = self.escalations.get(escalation_id)
        if e is None or e.status != "open":
            return False
        e.status, e.acknowledged_at = "acknowledged", _now()
        return True

    async def mark_alerted(self, escalation_id: str) -> None:
        e = self.escalations[escalation_id]
        e.alert_count += 1
        e.last_alerted_at = _now()

    async def escalations_to_reping(self, stale_before: datetime) -> list[Escalation]:
        return [
            replace(e)
            for e in self.escalations.values()
            if e.status == "open"
            and e.severity == 1
            and e.last_alerted_at is not None
            and e.last_alerted_at < stale_before
        ]

    async def resolve_escalation(self, escalation_id: str, hand_back: bool) -> bool:
        e = self.escalations.get(escalation_id)
        if e is None or e.status not in ("open", "acknowledged"):
            return False
        e.status = "handed_back" if hand_back else "resolved"
        if self.users[e.user_id].state == "escalated":  # not, e.g., after STOP
            self.users[e.user_id].state = self._prior_state.get(escalation_id, "active")
        return True

    async def aclose(self) -> None:
        return None

    # --- admin console -----------------------------------------------------------------

    async def get_admin(self, email: str) -> AdminUser | None:
        return self.admins.get(email.lower())

    async def add_admin(self, email: str, role: AdminRole) -> AdminUser:
        email = email.lower()
        old = self.admins.get(email)
        admin = AdminUser(old.id if old else str(uuid4()), email, role)
        self.admins[email] = admin
        return admin

    def _balance(self, user_id: str) -> int:
        return sum(e.delta for e in self.ledger[user_id])

    def _last_in(self, user_id: str) -> datetime | None:
        inbound = (m.created_at for m in self.messages[user_id] if m.direction == "in")
        return max(inbound, default=None)

    def _onboarded(self, user_id: str) -> bool:
        return any(uid == user_id for uid, _ in self.charts)

    def _row(self, u: User) -> UserRow:
        return UserRow(
            u.id,
            u.state,
            u.language,
            u.created_at,
            self._last_in(u.id),
            self._balance(u.id),
            self._onboarded(u.id),
        )

    async def list_users(
        self,
        *,
        state: UserState | None = None,
        wa_hash: str | None = None,
        before: datetime | None = None,
        limit: int = 50,
    ) -> list[UserRow]:
        users = list(self.users.values())
        if wa_hash is not None:
            uid = self.by_hash.get(wa_hash)
            users = [self.users[uid]] if uid else []
        users = [
            u
            for u in users
            if u.id not in self.erased
            and (state is None or u.state == state)
            and (before is None or u.created_at < before)
        ]
        users.sort(key=lambda u: u.created_at, reverse=True)
        return [self._row(u) for u in users[:limit]]

    def _esc_row(self, e: Escalation) -> EscalationRow:
        u = self.users[e.user_id]
        closed = self.closed.get(e.id)
        return EscalationRow(
            replace(e),
            u.state,
            u.language,
            self._last_in(u.id),
            closed[0] if closed else None,
            closed[2] if closed else None,
        )

    async def user_record(self, user_id: str) -> UserRecord | None:
        u = self.users.get(user_id)
        if u is None:
            return None
        escalations = sorted(
            (e for e in self.escalations.values() if e.user_id == user_id),
            key=lambda e: e.opened_at,
            reverse=True,
        )
        return UserRecord(
            self._row(u),
            self.births.get(user_id),
            list(self.consents[user_id]),
            [self._esc_row(e) for e in escalations],
            list(reversed(self.ledger[user_id])),
        )

    async def wa_id_enc(self, user_id: str) -> bytes | None:
        return self.wa_ids.get(user_id)

    async def messages_page(
        self, user_id: str, *, before_id: int | None = None, limit: int = 50
    ) -> list[AdminMessage]:
        msgs = [m for m in self.messages[user_id] if before_id is None or m.id < before_id]
        return msgs[-limit:]

    async def list_feedback(
        self, *, rating: Rating | None = None, before_id: int | None = None, limit: int = 50
    ) -> list[FeedbackRow]:
        rows = []
        for (uid, turn), (fid, r, at) in self.feedback.items():
            if uid in self.erased or (rating is not None and r != rating):
                continue
            if before_id is not None and fid >= before_id:
                continue
            question, answer = self.turn_texts.get((uid, turn), (None, None))
            lang = self.users[uid].language
            rows.append(FeedbackRow(fid, uid, turn, r, at, lang, question, answer))
        rows.sort(key=lambda f: f.id, reverse=True)
        return rows[:limit]

    async def set_blocked(self, user_id: str, blocked: bool) -> UserState | None:
        u = self.users.get(user_id)
        if u is None:
            return None
        if blocked:
            if u.state in ("escalated", "blocked"):
                return None
            u.state = "blocked"
        else:
            if u.state != "blocked":
                return None
            u.state = "active" if self._onboarded(user_id) else "new"
            u.onboarding = {}
        return u.state

    async def list_escalations(self, *, active: bool, limit: int = 100) -> list[EscalationRow]:
        live = ("open", "acknowledged")
        found = [e for e in self.escalations.values() if (e.status in live) == active]
        if active:
            found.sort(key=lambda e: (e.severity, e.opened_at))
        else:
            found.sort(key=lambda e: self.closed[e.id][0], reverse=True)
        return [self._esc_row(e) for e in found[:limit]]

    async def escalation_row(self, escalation_id: str) -> EscalationRow | None:
        e = self.escalations.get(escalation_id)
        return self._esc_row(e) if e else None

    async def close_escalation(
        self, escalation_id: str, *, hand_back: bool, by: str, note: str | None
    ) -> bool:
        if not await self.resolve_escalation(escalation_id, hand_back):
            return False
        self.closed[escalation_id] = (_now(), by, note)
        return True

    async def log_human_message(
        self,
        user_id: str,
        turn_id: str,
        *,
        kind: str,
        body: str | None,
        meta: dict[str, Any],
        admin_id: str,
    ) -> bool:
        if turn_id in self.human_turns or turn_id in self.replies:
            return False
        self.human_turns.add(turn_id)
        self._log(user_id, "out", "human", kind, body, _now(), meta)
        return True

    async def add_credits(
        self,
        user_id: str,
        delta: int,
        reason: CreditReason,
        idempotency_key: str,
        ref: dict[str, Any] | None = None,
    ) -> bool:
        if delta == 0:
            raise ValueError("delta must not be zero")
        if idempotency_key in self.ledger_keys:
            return False
        self.ledger_keys[idempotency_key] = (user_id, delta)
        self.ledger[user_id].append(LedgerEntry(delta, reason, _now(), ref))
        return True

    async def get_config(self, key: str) -> Any | None:
        entry = self.config.get(key)
        return entry.value if entry else None

    async def config_entries(self) -> list[ConfigEntry]:
        return sorted(self.config.values(), key=lambda e: e.key)

    async def set_config(self, key: str, value: Any, by: str) -> None:
        self.config[key] = ConfigEntry(key, value, _now(), by)

    async def audit(
        self,
        actor: str,
        action: str,
        subject_user_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.audit_log.append(AuditEntry(actor, action, subject_user_id, detail, _now()))

    async def audit_entries(
        self, *, subject_user_id: str | None = None, limit: int = 100
    ) -> list[AuditEntry]:
        found = [
            a
            for a in reversed(self.audit_log)
            if subject_user_id is None or a.subject_user_id == subject_user_id
        ]
        return found[:limit]

    async def metrics(self, now: datetime, days: int) -> Metrics:
        first = ist_day(now) - timedelta(days=days - 1)
        per_day: dict[date, Counter[str]] = {first + timedelta(d): Counter() for d in range(days)}

        def bump(t: datetime, what: str) -> None:
            if (d := ist_day(t)) in per_day:
                per_day[d][what] += 1

        for u in self.users.values():
            bump(u.created_at, "new_users")
        for at in self.chart_at.values():
            bump(at, "onboarded")
        for msgs in self.messages.values():
            for m in msgs:
                bump(m.created_at, "messages_in" if m.direction == "in" else "messages_out")
        for e in self.escalations.values():
            bump(e.opened_at, "escalations")

        paid = {
            uid
            for uid, entries in self.ledger.items()
            if any(e.reason in ("purchase", "pass") for e in entries)
        }
        onboarded = {uid for uid, _ in self.charts}
        consented = {
            uid
            for uid, cs in self.consents.items()
            if any(c.granted and c.purpose == "readings" for c in cs)
        }
        since = now - timedelta(days=days)
        acks = [
            (e.acknowledged_at - e.opened_at).total_seconds() / 60
            for e in self.escalations.values()
            if e.acknowledged_at is not None and e.opened_at >= since
        ]
        by_source: dict[str, set[str]] = defaultdict(set)
        for uid, refs in self.referrals.items():
            for r in refs:
                if r.get("source_id"):
                    by_source[str(r["source_id"])].add(uid)
        ads = sorted(
            (
                AdStats(src, len(uids), len(uids & onboarded), len(uids & paid))
                for src, uids in by_source.items()
            ),
            key=lambda a: (-a.users, a.source_id),
        )[:20]
        return Metrics(
            users_by_state=dict(Counter(u.state for u in self.users.values())),
            funnel={
                "started": len(self.users),
                "consented": len(consented),
                "onboarded": len(onboarded),
                "paid": len(paid),
            },
            days=[DayStats(d, **c) for d, c in sorted(per_day.items())],
            open_escalations=dict(
                Counter(
                    e.category
                    for e in self.escalations.values()
                    if e.status in ("open", "acknowledged")
                )
            ),
            median_ack_minutes=statistics.median(acks) if acks else None,
            ads=ads,
        )
