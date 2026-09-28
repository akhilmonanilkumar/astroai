"""Business knobs in the `app_config` table: typed schemas, defaults and a cached reader.

The admin console edits these without a deploy. Every key has a schema here; the console
can only save values that validate, and unknown keys are refused. `DEFAULTS` mirrors the
migration seeds so the in-memory store (dev/test) behaves like a fresh database.
"""

import re
import time
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pack(_Strict):
    id: str = Field(min_length=1, max_length=32)
    price_inr: int = Field(gt=0)
    prashnas: int = Field(gt=0)
    show_once: bool = False
    badge: str | None = Field(default=None, max_length=24)


class Pass(_Strict):
    id: str = Field(min_length=1, max_length=32)
    price_inr: int = Field(gt=0)
    days: int = Field(gt=0)


class FreeTier(_Strict):
    welcome_hours: int = Field(ge=0, le=168)
    welcome_prashnas: int = Field(ge=0)
    daily_free_answers: int = Field(ge=0)


class Prashna(_Strict):
    followups: int = Field(ge=0, le=10)
    voice_credit_cost: int = Field(ge=1, le=10)
    # A follow-up after this long starts a new prashna.
    followup_hours: int = Field(default=12, ge=1, le=72)


class PlusLimits(_Strict):
    prashnas_per_day: int = Field(ge=1)


class Flags(_Strict):
    # busy_mode is the viral-spike playbook (not built yet). new_user_admission=false
    # waitlists every new user except those with an invite code (see Beta).
    busy_mode: bool = False
    voice_enabled: bool = True
    new_user_admission: bool = True


class Beta(_Strict):
    """Closed beta: with invite_only, a new user needs one of `codes` in a message before
    onboarding starts; everyone else gets a scripted waitlist reply. Users who have
    consented are never affected, and safety replies and privacy commands always work."""

    invite_only: bool = False
    codes: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _plain_codes(self) -> "Beta":
        for code in self.codes:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{3,31}", code):
                raise ValueError(f"code {code!r}: 4-32 letters, digits or dashes")
        if self.invite_only and not self.codes:
            raise ValueError("invite_only needs at least one code")
        return self


class Retention(_Strict):
    opted_out_days: int = Field(ge=1, le=3650)
    message_days: int = Field(ge=30, le=3650)
    pending_order_hours: int = Field(ge=1, le=720)


class HumanTemplate(_Strict):
    """Approved WhatsApp utility template for team replies outside the 24-hour window."""

    name: str = Field(min_length=1, max_length=512, pattern=r"^[a-z0-9_]+$")
    languages: dict[str, str]

    @model_validator(mode="after")
    def _covers_every_language(self) -> "HumanTemplate":
        missing = {"en", "hinglish", "hi"} - self.languages.keys()
        if missing:
            raise ValueError(f"languages must map {sorted(missing)} to a template language")
        return self


def _unique_ids(items: list[Any]) -> list[Any]:
    ids = [i.id for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError("ids must be unique")
    return items


SCHEMAS: dict[str, TypeAdapter[Any]] = {
    "packs": TypeAdapter(list[Pack]),
    "passes": TypeAdapter(list[Pass]),
    "free_tier": TypeAdapter(FreeTier),
    "prashna": TypeAdapter(Prashna),
    "plus_limits": TypeAdapter(PlusLimits),
    "flags": TypeAdapter(Flags),
    "human_template": TypeAdapter(HumanTemplate),
    "retention": TypeAdapter(Retention),
    "beta": TypeAdapter(Beta),
}

DEFAULTS: dict[str, Any] = {
    "packs": [
        {"id": "trial", "price_inr": 11, "prashnas": 2, "show_once": True},
        {"id": "p51", "price_inr": 51, "prashnas": 10},
        {"id": "p101", "price_inr": 101, "prashnas": 25, "badge": "Most chosen"},
        {"id": "p251", "price_inr": 251, "prashnas": 70},
        {"id": "p501", "price_inr": 501, "prashnas": 160},
    ],
    "passes": [
        {"id": "plus_monthly", "price_inr": 199, "days": 30},
        {"id": "plus_quarterly", "price_inr": 501, "days": 90},
        {"id": "plus_yearly", "price_inr": 1501, "days": 365},
    ],
    "free_tier": {"welcome_hours": 72, "welcome_prashnas": 5, "daily_free_answers": 1},
    "prashna": {"followups": 3, "voice_credit_cost": 2, "followup_hours": 12},
    "plus_limits": {"prashnas_per_day": 5},
    "flags": {"busy_mode": False, "voice_enabled": True, "new_user_admission": True},
    "retention": {"opted_out_days": 180, "message_days": 730, "pending_order_hours": 48},
    "beta": {"invite_only": False, "codes": []},
    "human_template": {
        "name": "team_followup",
        "languages": {"en": "en", "hinglish": "en", "hi": "hi"},
    },
}


class ConfigError(ValueError):
    pass


def validate(key: str, value: Any) -> Any:
    """The value as it will be stored (JSON), or ConfigError with a readable reason."""
    schema = SCHEMAS.get(key)
    if schema is None:
        raise ConfigError(f"unknown config key {key!r}")
    try:
        parsed = schema.validate_python(value)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'value'}: {err['msg']}" for err in e.errors()
        )
        raise ConfigError(problems) from e
    if isinstance(parsed, list):
        try:
            _unique_ids(parsed)
        except ValueError as e:
            raise ConfigError(str(e)) from e
    return schema.dump_python(parsed, mode="json", exclude_none=True)


class ConfigSource(Protocol):
    async def get_config(self, key: str) -> Any | None: ...


class ConfigReader:
    """Workers read knobs through this: a short cache so edits apply within `ttl` seconds."""

    def __init__(self, source: ConfigSource, ttl: float = 30.0) -> None:
        self.source = source
        self.ttl = ttl
        self._cache: dict[str, tuple[float, Any]] = {}

    async def get(self, key: str) -> Any:
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit is not None and now - hit[0] < self.ttl:
            return hit[1]
        raw = await self.source.get_config(key)
        try:
            value = SCHEMAS[key].validate_python(raw if raw is not None else DEFAULTS[key])
        except ValidationError:
            value = SCHEMAS[key].validate_python(DEFAULTS[key])  # a bad row never stops turns
        self._cache[key] = (now, value)
        return value

    async def flags(self) -> Flags:
        flags: Flags = await self.get("flags")
        return flags
