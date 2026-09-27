"""Rows the app reads and the per-turn write set it commits."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

UserState = Literal["new", "consented", "onboarding", "active", "escalated", "blocked"]
FactCategory = Literal[
    "career",
    "relationship",
    "family",
    "health",
    "education",
    "finance",
    "relocation",
    "spiritual",
    "other",
]
FACT_CATEGORIES: tuple[str, ...] = FactCategory.__args__  # type: ignore[attr-defined]


@dataclass
class User:
    id: str
    state: UserState
    language: str | None
    onboarding: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class EncryptedBirth:
    """birth_details row; every personal value is a guruji.crypto blob."""

    name_enc: bytes | None
    date_enc: bytes
    time_enc: bytes | None
    time_known: bool
    place_enc: bytes
    latitude_enc: bytes
    longitude_enc: bytes
    tz_name: str


@dataclass(frozen=True)
class LoggedMessage:
    direction: Literal["in", "out"]
    sent_by: Literal["user", "guru", "human"]
    kind: str
    body: str | None
    created_at: datetime
    meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class LifeFact:
    category: str
    fact: str
    created_at: datetime


@dataclass(frozen=True)
class Reading:
    topic: str
    summary: str
    factors: list[str]
    created_at: datetime


@dataclass(frozen=True)
class Consent:
    notice_version: str
    purpose: Literal["readings", "marketing"]
    granted: bool
    age_confirmed: bool
    wamid: str
    given_at: datetime


EscalationCategory = Literal[
    "crisis",
    "medical",
    "legal",
    "abuse",
    "human_requested",
    "dissatisfied",
    "payment_dispute",
    "out_of_scope",
]
EscalationStatus = Literal["open", "acknowledged", "resolved", "handed_back"]


@dataclass(frozen=True)
class EscalationOpen:
    """Opened by a turn. The id is made by the app so the alert can reference it."""

    id: str
    category: EscalationCategory
    severity: int  # 1 = most urgent
    prior_state: UserState = "active"


@dataclass
class Escalation:
    id: str
    user_id: str
    category: EscalationCategory
    severity: int
    status: EscalationStatus
    alert_count: int
    opened_at: datetime
    acknowledged_at: datetime | None = None
    last_alerted_at: datetime | None = None


@dataclass(frozen=True)
class InboundLog:
    wamid: str
    kind: str
    body: str | None


@dataclass
class TurnWrite:
    """Everything one turn changes, committed atomically and at most once per turn_id."""

    user_id: str
    turn_id: str
    inbound: list[InboundLog]
    reply_body: str | None = None
    reply_meta: dict[str, Any] | None = None
    state: UserState | None = None
    language: str | None = None
    onboarding: dict[str, Any] | None = None
    consents: list[Consent] = field(default_factory=list)
    birth: EncryptedBirth | None = None
    chart: tuple[str, dict[str, Any]] | None = None  # (engine_version, dossier JSON)
    facts: list[tuple[str, str]] = field(default_factory=list)  # (category, fact)
    readings: list[tuple[str, str, list[str]]] = field(default_factory=list)
    referral: dict[str, Any] | None = None
    escalation: EscalationOpen | None = None


@dataclass(frozen=True)
class StoredReply:
    body: str
    meta: dict[str, Any] | None
