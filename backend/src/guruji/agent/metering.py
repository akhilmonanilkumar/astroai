"""Who pays for a question: free allowances, Guru Plus, or credits.

The unit is a prashna: one reading question plus a few follow-ups on it, within some hours.
Small talk (greetings, thanks, clarifying) is never metered. For a new prashna the first
entitlement that applies wins:

  1. welcome   first `welcome_hours` after the user's first message, `welcome_prashnas`
  2. plus      an active Guru Plus pass, up to `prashnas_per_day`
  3. daily     after the welcome window, `daily_free_answers` short text answers a day
  4. credits   1 credit (voice replies cost `voice_credit_cost`); the first paid question of
               a day asks before spending, later ones that day say what they used
  5. empty     nothing left: offer a top-up, never answer on credit

Pure: the turn graph loads the meter (users.meter), balance and pass, and commits the
updated meter and any ledger row with the reply, only if the answer succeeds.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from guruji.appconfig import FreeTier, PlusLimits, Prashna

_IST = ZoneInfo("Asia/Kolkata")

Paid = Literal["welcome", "plus", "daily", "credit"]
Kind = Literal["followup", "welcome", "plus", "daily", "charge", "confirm", "empty"]


def ist_day(t: datetime) -> str:
    return t.astimezone(_IST).date().isoformat()


@dataclass(frozen=True)
class Meter:
    """users.meter, typed. Unknown or missing keys fall back to a fresh meter."""

    prashna_id: str | None = None
    prashna_opened: datetime | None = None
    followups_left: int = 0
    paid_by: Paid | None = None
    welcome_used: int = 0
    day: str | None = None
    day_plus_used: int = 0
    day_free_used: int = 0
    spend_ok_day: str | None = None
    pending: dict[str, Any] | None = None  # {"text", "voice", "at"}: awaiting a yes

    @classmethod
    def load(cls, raw: dict[str, Any] | None) -> "Meter":
        raw = raw or {}
        p = raw.get("prashna") or {}
        opened = p.get("opened")
        return cls(
            prashna_id=p.get("id"),
            prashna_opened=datetime.fromisoformat(opened) if opened else None,
            followups_left=int(p.get("followups_left", 0)),
            paid_by=p.get("paid_by"),
            welcome_used=int(raw.get("welcome_used", 0)),
            day=raw.get("day"),
            day_plus_used=int(raw.get("day_plus_used", 0)),
            day_free_used=int(raw.get("day_free_used", 0)),
            spend_ok_day=raw.get("spend_ok_day"),
            pending=raw.get("pending"),
        )

    def dump(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "welcome_used": self.welcome_used,
            "day": self.day,
            "day_plus_used": self.day_plus_used,
            "day_free_used": self.day_free_used,
            "spend_ok_day": self.spend_ok_day,
        }
        if self.prashna_id:
            out["prashna"] = {
                "id": self.prashna_id,
                "opened": self.prashna_opened.isoformat() if self.prashna_opened else None,
                "followups_left": self.followups_left,
                "paid_by": self.paid_by,
            }
        if self.pending:
            out["pending"] = self.pending
        return out

    def on(self, now: datetime) -> "Meter":
        """The meter as of `now`: daily counters reset at IST midnight."""
        today = ist_day(now)
        if self.day == today:
            return self
        return replace(self, day=today, day_plus_used=0, day_free_used=0)


@dataclass(frozen=True)
class Rules:
    free: FreeTier
    prashna: Prashna
    plus: PlusLimits


@dataclass(frozen=True)
class Decision:
    kind: Kind
    cost: int = 0  # credits this answer spends (charge) or would spend (confirm)
    voice: bool = False  # answer as a voice note
    brief: bool = False  # the daily free answer is kept short
    meter: Meter = field(default_factory=Meter)  # to commit if the answer succeeds
    balance: int = 0  # credits before this answer

    @property
    def answers(self) -> bool:
        return self.kind in ("followup", "welcome", "plus", "daily", "charge")


def decide(
    meter: Meter,
    rules: Rules,
    *,
    now: datetime,
    joined: datetime,
    turn_id: str,
    balance: int,
    plus_active: bool,
    voice: bool,
    accepted: bool = False,
) -> Decision:
    """What this reading question costs. `accepted`: the user just said yes to the cost."""
    d = _decide(meter, rules, now, joined, turn_id, balance, plus_active, voice, accepted)
    return replace(d, balance=balance)


def _decide(
    meter: Meter,
    rules: Rules,
    now: datetime,
    joined: datetime,
    turn_id: str,
    balance: int,
    plus_active: bool,
    voice: bool,
    accepted: bool,
) -> Decision:
    m = meter.on(now)
    open_until = (
        m.prashna_opened + timedelta(hours=rules.prashna.followup_hours)
        if m.prashna_opened
        else None
    )
    if m.prashna_id and m.followups_left > 0 and open_until and now < open_until:
        # a follow-up is free, but a voice reply is only covered where the prashna's was
        spoken = voice and m.paid_by in ("plus", "credit", "welcome")
        return Decision(
            "followup", voice=spoken, meter=replace(m, followups_left=m.followups_left - 1)
        )

    def opened(paid_by: Paid, **changes: Any) -> Meter:
        return replace(
            m,
            prashna_id=turn_id,
            prashna_opened=now,
            followups_left=rules.prashna.followups,
            paid_by=paid_by,
            pending=None,
            **changes,
        )

    in_welcome = now - joined < timedelta(hours=rules.free.welcome_hours)
    if in_welcome and m.welcome_used < rules.free.welcome_prashnas:
        return Decision(
            "welcome", voice=voice, meter=opened("welcome", welcome_used=m.welcome_used + 1)
        )
    if plus_active and m.day_plus_used < rules.plus.prashnas_per_day:
        return Decision(
            "plus", voice=voice, meter=opened("plus", day_plus_used=m.day_plus_used + 1)
        )
    if not in_welcome and m.day_free_used < rules.free.daily_free_answers:
        return Decision(
            "daily", brief=True, meter=opened("daily", day_free_used=m.day_free_used + 1)
        )
    cost = rules.prashna.voice_credit_cost if voice else 1
    if voice and balance < cost:
        voice, cost = False, 1  # enough for text only: answer in text
    if balance < cost:
        return Decision("empty", cost=cost, meter=replace(m, pending=None))
    today = ist_day(now)
    if accepted or m.spend_ok_day == today:
        return Decision(
            "charge", cost=cost, voice=voice, meter=opened("credit", spend_ok_day=today)
        )
    return Decision("confirm", cost=cost, voice=voice, meter=m)
