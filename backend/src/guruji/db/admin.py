"""What the admin console reads and changes: rows, the `AdminStore` protocol, metrics.

Implemented by both stores (Postgres and memory) and exercised by one contract test.
Message bodies are plain text in the database; personal fields (birth details, phone
number) stay encrypted here and are decrypted only by the admin API, on an owner's
explicit, audited request.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol

from guruji.db.models import Consent, EncryptedBirth, Escalation, UserState

AdminRole = Literal["owner", "agent"]
CreditReason = Literal[
    "welcome", "daily_free", "purchase", "pass", "spend", "refund", "adjust", "expire"
]


@dataclass(frozen=True)
class AdminUser:
    id: str
    email: str
    role: AdminRole
    disabled: bool = False


@dataclass(frozen=True)
class UserRow:
    id: str
    state: UserState
    language: str | None
    created_at: datetime
    last_inbound_at: datetime | None
    balance: int
    onboarded: bool  # has a chart


@dataclass(frozen=True)
class EscalationRow:
    escalation: Escalation
    user_state: UserState
    language: str | None
    last_inbound_at: datetime | None
    resolved_at: datetime | None = None
    notes: str | None = None


@dataclass(frozen=True)
class AdminMessage:
    id: int
    direction: Literal["in", "out"]
    sent_by: Literal["user", "guru", "human"]
    kind: str
    body: str | None
    created_at: datetime
    meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class LedgerEntry:
    delta: int
    reason: str
    created_at: datetime
    ref: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConfigEntry:
    key: str
    value: Any
    updated_at: datetime
    updated_by: str


@dataclass(frozen=True)
class AuditEntry:
    actor: str
    action: str
    subject_user_id: str | None
    detail: dict[str, Any] | None
    created_at: datetime


@dataclass(frozen=True)
class UserRecord:
    """One user's full record for the console. Birth details are still encrypted."""

    row: UserRow
    birth: EncryptedBirth | None
    consents: list[Consent]
    escalations: list[EscalationRow]
    ledger: list[LedgerEntry]


@dataclass(frozen=True)
class DayStats:
    day: date  # IST
    new_users: int = 0
    onboarded: int = 0
    messages_in: int = 0
    messages_out: int = 0
    escalations: int = 0


@dataclass(frozen=True)
class AdStats:
    source_id: str
    users: int
    onboarded: int
    paid: int


@dataclass(frozen=True)
class Metrics:
    users_by_state: dict[str, int]
    funnel: dict[str, int]  # started → consented → onboarded → paid
    days: list[DayStats]
    open_escalations: dict[str, int]  # by category
    median_ack_minutes: float | None  # over the window
    ads: list[AdStats] = field(default_factory=list)


class AdminStore(Protocol):
    # --- team ------------------------------------------------------------------------
    async def get_admin(self, email: str) -> AdminUser | None: ...
    async def add_admin(self, email: str, role: AdminRole) -> AdminUser:
        """Create or update (and re-enable) a team member."""
        ...

    # --- users -----------------------------------------------------------------------
    async def list_users(
        self,
        *,
        state: UserState | None = None,
        wa_hash: str | None = None,
        before: datetime | None = None,
        limit: int = 50,
    ) -> list[UserRow]:
        """Newest first; `before` pages by created_at. Deleted users are left out."""
        ...

    async def user_record(self, user_id: str) -> UserRecord | None: ...
    async def wa_id_enc(self, user_id: str) -> bytes | None: ...
    async def messages_page(
        self, user_id: str, *, before_id: int | None = None, limit: int = 50
    ) -> list[AdminMessage]:
        """Oldest first, the `limit` messages just before `before_id` (or the latest)."""
        ...

    async def set_blocked(self, user_id: str, blocked: bool) -> UserState | None:
        """Block, or unblock back to active (onboarded) or new; None if not allowed now.

        Escalated users can't be blocked: close the case first.
        """
        ...

    # --- escalations ------------------------------------------------------------------
    async def list_escalations(self, *, active: bool, limit: int = 100) -> list[EscalationRow]:
        """Active: most urgent then oldest first. Closed: most recently closed first."""
        ...

    async def escalation_row(self, escalation_id: str) -> EscalationRow | None: ...
    async def close_escalation(
        self, escalation_id: str, *, hand_back: bool, by: str, note: str | None
    ) -> bool:
        """Resolve (or hand back) with who and why; the user returns to their prior state."""
        ...

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
        """Log a team message once per turn_id; False if it was already logged."""
        ...

    # --- credits ---------------------------------------------------------------------
    async def add_credits(
        self,
        user_id: str,
        delta: int,
        reason: CreditReason,
        idempotency_key: str,
        ref: dict[str, Any] | None = None,
    ) -> bool:
        """Append to the ledger; False if the key was already used."""
        ...

    # --- config and audit -----------------------------------------------------------
    async def get_config(self, key: str) -> Any | None: ...
    async def config_entries(self) -> list[ConfigEntry]: ...
    async def set_config(self, key: str, value: Any, by: str) -> None: ...
    async def audit(
        self,
        actor: str,
        action: str,
        subject_user_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None: ...
    async def audit_entries(
        self, *, subject_user_id: str | None = None, limit: int = 100
    ) -> list[AuditEntry]: ...

    # --- metrics ---------------------------------------------------------------------
    async def metrics(self, now: datetime, days: int) -> Metrics: ...
