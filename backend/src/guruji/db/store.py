"""The `Store` protocol and an in-process implementation for dev and tests.

The turn worker reads through the store and writes one `TurnWrite` per turn. Commits
are idempotent by turn_id, so a redelivered turn replays the stored reply instead of
running the guru (and its side effects) twice.
"""

from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from guruji.db.models import (
    Consent,
    EncryptedBirth,
    Escalation,
    LifeFact,
    LoggedMessage,
    Reading,
    StoredReply,
    TurnWrite,
    User,
    UserState,
)


class Store(Protocol):
    async def get_or_create_user(self, wa_hash: str) -> tuple[User, bool]: ...
    async def set_wa_id(self, user_id: str, wa_id_enc: bytes) -> None: ...
    async def get_birth(self, user_id: str) -> EncryptedBirth | None: ...
    async def get_chart(self, user_id: str, engine_version: str) -> dict[str, Any] | None: ...
    async def recent_messages(self, user_id: str, limit: int) -> list[LoggedMessage]: ...
    async def facts(self, user_id: str, limit: int) -> list[LifeFact]: ...
    async def readings(self, user_id: str, limit: int) -> list[Reading]: ...
    async def stored_reply(self, turn_id: str) -> StoredReply | None: ...
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


class MemoryStore:
    """Dict-backed store. Single process only (the `dev` role and tests)."""

    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.by_hash: dict[str, str] = {}
        self.wa_ids: dict[str, bytes] = {}
        self.births: dict[str, EncryptedBirth] = {}
        self.charts: dict[tuple[str, str], dict[str, Any]] = {}
        self.messages: dict[str, list[LoggedMessage]] = defaultdict(list)
        self.inbound_wamids: set[str] = set()
        self.replies: dict[str, StoredReply] = {}
        self.consents: dict[str, list[Consent]] = defaultdict(list)
        self.life_facts: dict[str, list[LifeFact]] = defaultdict(list)
        self.reading_log: dict[str, list[Reading]] = defaultdict(list)
        self.referrals: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.escalations: dict[str, Escalation] = {}
        self._prior_state: dict[str, UserState] = {}

    async def get_or_create_user(self, wa_hash: str) -> tuple[User, bool]:
        if wa_hash in self.by_hash:
            return replace(self.users[self.by_hash[wa_hash]]), False
        user = User(id=str(uuid4()), state="new", language=None, onboarding={}, created_at=_now())
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
        return self.messages[user_id][-limit:]

    async def facts(self, user_id: str, limit: int) -> list[LifeFact]:
        return self.life_facts[user_id][-limit:]

    async def readings(self, user_id: str, limit: int) -> list[Reading]:
        return self.reading_log[user_id][-limit:]

    async def stored_reply(self, turn_id: str) -> StoredReply | None:
        return self.replies.get(turn_id)

    async def commit_turn(self, w: TurnWrite) -> bool:
        if w.turn_id in self.replies:
            return False
        now = _now()
        for m in w.inbound:
            if m.wamid not in self.inbound_wamids:
                self.inbound_wamids.add(m.wamid)
                self.messages[w.user_id].append(LoggedMessage("in", "user", m.kind, m.body, now))
        user = self.users[w.user_id]
        if w.state is not None:
            user.state = w.state
        if w.language is not None:
            user.language = w.language
        if w.onboarding is not None:
            user.onboarding = dict(w.onboarding)
        self.consents[w.user_id].extend(w.consents)
        if w.birth is not None:
            self.births[w.user_id] = w.birth
        if w.chart is not None:
            self.charts[(w.user_id, w.chart[0])] = w.chart[1]
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
            self.messages[w.user_id].append(
                LoggedMessage("out", "guru", "text", w.reply_body, now, w.reply_meta)
            )
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
        self.users[e.user_id].state = self._prior_state.get(escalation_id, "active")
        return True

    async def aclose(self) -> None:
        return None
