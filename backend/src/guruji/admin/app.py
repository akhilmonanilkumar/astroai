"""Admin console API: inbox and human replies, users, metrics, config, audit log.

Only the console calls this (through its own `/api` proxy). Every request carries a team
member's token (see `auth`). Rules this module keeps:

- Every view of a user's data is written to `audit_log` (repeat views by the same person
  within half an hour count once, so the console can poll). Actors are "admin:<id>".
- Birth details and the phone number stay masked; only an owner can reveal them, and each
  reveal is logged.
- Team replies go out through the sender queue like Guruji's, labelled as the team, only
  while an escalation is open, and only inside WhatsApp's 24-hour window; after it closes,
  the approved utility template (app_config `human_template`) is the only option.
- Nothing personal goes to the log: ids and counts only.
"""

import logging
import re
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from guruji import appconfig
from guruji.admin.auth import AuthError, Verifier
from guruji.config import Settings
from guruji.crypto import FieldCipher, decode_key, lookup_hash
from guruji.db.admin import AdminUser, EscalationRow, UserRow
from guruji.db.models import UserState
from guruji.db.store import Store
from guruji.queue.streams import Queue, enqueue
from guruji.safety.messages import TEAM_LABEL

log = logging.getLogger(__name__)

# WhatsApp's customer-service window: free-form messages only this long after the user's
# last message. A Meta rule, not a business knob.
REPLY_WINDOW = timedelta(hours=24)
AUDIT_DEDUPE_SECONDS = 1800
MAX_REPLY_CHARS = 3000
_CLIENT_ID = re.compile(r"^[0-9a-f]{32}$")


class ReplyIn(BaseModel):
    client_id: str = Field(pattern=_CLIENT_ID.pattern)  # makes a retried send idempotent
    text: str = Field(min_length=1, max_length=MAX_REPLY_CHARS)


class TemplateIn(BaseModel):
    client_id: str = Field(pattern=_CLIENT_ID.pattern)


class CloseIn(BaseModel):
    hand_back: bool
    note: str | None = Field(default=None, max_length=2000)


class CreditsIn(BaseModel):
    client_id: str = Field(pattern=_CLIENT_ID.pattern)
    delta: int = Field(ge=-1000, le=1000)
    note: str = Field(min_length=1, max_length=500)


class ConfigIn(BaseModel):
    value: Any


def actor(admin: AdminUser) -> str:
    return f"admin:{admin.id}"


def window_open(last_inbound_at: datetime | None, now: datetime) -> bool:
    return last_inbound_at is not None and now - last_inbound_at < REPLY_WINDOW


def normalize_phone(raw: str) -> str | None:
    """WhatsApp ids are digits with the country code; a bare 10-digit number is Indian."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "91" + digits
    return digits if 11 <= len(digits) <= 15 else None


def _user_json(row: UserRow, now: datetime) -> dict[str, Any]:
    return {**asdict(row), "window_open": window_open(row.last_inbound_at, now)}


def _escalation_json(row: EscalationRow, now: datetime) -> dict[str, Any]:
    e = row.escalation
    return {
        "id": e.id,
        "user_id": e.user_id,
        "category": e.category,
        "severity": e.severity,
        "status": e.status,
        "alert_count": e.alert_count,
        "opened_at": e.opened_at,
        "acknowledged_at": e.acknowledged_at,
        "resolved_at": row.resolved_at,
        "notes": row.notes,
        "user_state": row.user_state,
        "language": row.language,
        "last_inbound_at": row.last_inbound_at,
        "window_open": window_open(row.last_inbound_at, now),
    }


def create_admin_app(
    settings: Settings,
    store: Store,
    redis: Redis,
    verifier: Verifier,
    clock: Any = None,
) -> FastAPI:
    app = FastAPI(title="guruji-admin", docs_url=None, redoc_url=None, openapi_url=None)
    cipher = FieldCipher(decode_key(settings.field_encryption_key))
    lookup_key = decode_key(settings.lookup_hmac_key)
    now = clock or (lambda: datetime.now(UTC))

    async def current_admin(request: Request) -> AdminUser:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(401, "sign_in_required")
        try:
            who = await verifier.verify(header[7:])
        except AuthError as e:
            raise HTTPException(401, "sign_in_required") from e
        if not who.mfa:
            raise HTTPException(401, "mfa_required")
        admin = await store.get_admin(who.email)
        if admin is None and who.dev_role is not None:
            admin = await store.add_admin(who.email, who.dev_role)
        if admin is None or admin.disabled:
            raise HTTPException(403, "not_a_team_member")
        return admin

    async def owner(admin: Annotated[AdminUser, Depends(current_admin)]) -> AdminUser:
        if admin.role != "owner":
            raise HTTPException(403, "owner_only")
        return admin

    Admin = Annotated[AdminUser, Depends(current_admin)]
    Owner = Annotated[AdminUser, Depends(owner)]

    async def audit_view(admin: AdminUser, action: str, user_id: str) -> None:
        key = f"audit:{admin.id}:{action}:{user_id}"
        if await redis.set(key, "1", nx=True, ex=AUDIT_DEDUPE_SECONDS):
            await store.audit(actor(admin), action, user_id)

    async def active_escalation_row(escalation_id: str) -> EscalationRow:
        row = await _escalation(escalation_id)
        if row.escalation.status not in ("open", "acknowledged"):
            raise HTTPException(409, "escalation_closed")
        return row

    async def _escalation(escalation_id: str) -> EscalationRow:
        try:
            uuid.UUID(escalation_id)
        except ValueError as e:
            raise HTTPException(404, "not_found") from e
        row = await store.escalation_row(escalation_id)
        if row is None:
            raise HTTPException(404, "not_found")
        return row

    async def wa_id_of(user_id: str) -> str:
        blob = await store.wa_id_enc(user_id)
        if blob is None:
            raise HTTPException(409, "no_phone_number")  # e.g. a deleted user
        return cipher.decrypt("wa_id", blob, user_id)

    async def take_case(admin: AdminUser, row: EscalationRow) -> None:
        if row.escalation.status == "open":
            await store.acknowledge_escalation(row.escalation.id, actor(admin))

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        await redis.ping()
        return {"ok": True}

    @app.get("/api/me")
    async def me(admin: Admin) -> dict[str, Any]:
        return {"id": admin.id, "email": admin.email, "role": admin.role}

    # --- inbox --------------------------------------------------------------------------

    @app.get("/api/escalations")
    async def escalations(
        admin: Admin, status: Literal["active", "closed"] = "active"
    ) -> list[dict[str, Any]]:
        rows = await store.list_escalations(active=status == "active")
        t = now()
        return [_escalation_json(r, t) for r in rows]

    @app.get("/api/escalations/{escalation_id}")
    async def escalation(escalation_id: str, admin: Admin) -> dict[str, Any]:
        return _escalation_json(await _escalation(escalation_id), now())

    @app.post("/api/escalations/{escalation_id}/ack")
    async def acknowledge(escalation_id: str, admin: Admin) -> dict[str, Any]:
        row = await active_escalation_row(escalation_id)
        await take_case(admin, row)
        await store.audit(
            actor(admin), "escalation_ack", row.escalation.user_id, {"escalation": escalation_id}
        )
        return _escalation_json(await _escalation(escalation_id), now())

    @app.post("/api/escalations/{escalation_id}/close")
    async def close(escalation_id: str, body: CloseIn, admin: Admin) -> dict[str, Any]:
        row = await active_escalation_row(escalation_id)
        ok = await store.close_escalation(
            escalation_id, hand_back=body.hand_back, by=actor(admin), note=body.note
        )
        if not ok:
            raise HTTPException(409, "escalation_closed")
        await store.audit(
            actor(admin),
            "escalation_close",
            row.escalation.user_id,
            {"escalation": escalation_id, "hand_back": body.hand_back},
        )
        log.info("escalation %s closed hand_back=%s", escalation_id, body.hand_back)
        return _escalation_json(await _escalation(escalation_id), now())

    @app.post("/api/escalations/{escalation_id}/reply")
    async def reply(escalation_id: str, body: ReplyIn, admin: Admin) -> dict[str, Any]:
        row = await active_escalation_row(escalation_id)
        if not window_open(row.last_inbound_at, now()):
            raise HTTPException(409, "window_closed")
        user_id = row.escalation.user_id
        to = await wa_id_of(user_id)
        lang = row.language if row.language in TEAM_LABEL else "en"
        text = f"{TEAM_LABEL[lang]}\n{body.text.strip()}"
        turn_id = f"h_{body.client_id}"
        fresh = await store.log_human_message(
            user_id,
            turn_id,
            kind="text",
            body=text,
            meta={"kind": "human", "bubbles": [text], "buttons": []},
            admin_id=admin.id,
        )
        # Enqueue even when already logged: a retry after a failed enqueue must still send.
        await enqueue(
            redis,
            Queue.SEND,
            {"kind": "bubbles", "to": to, "turn_id": turn_id, "bubbles": [text], "buttons": []},
            job_id=f"send:{turn_id}",
        )
        if fresh:
            await take_case(admin, row)
            await store.audit(
                actor(admin),
                "human_reply",
                user_id,
                {"escalation": escalation_id, "turn_id": turn_id, "chars": len(body.text)},
            )
        return {"turn_id": turn_id, "queued": True}

    @app.post("/api/escalations/{escalation_id}/template")
    async def template(escalation_id: str, body: TemplateIn, admin: Admin) -> dict[str, Any]:
        row = await active_escalation_row(escalation_id)
        user_id = row.escalation.user_id
        tpl = appconfig.SCHEMAS["human_template"].validate_python(
            await store.get_config("human_template") or appconfig.DEFAULTS["human_template"]
        )
        language = tpl.languages.get(row.language or "en", tpl.languages["en"])
        to = await wa_id_of(user_id)
        turn_id = f"h_{body.client_id}"
        fresh = await store.log_human_message(
            user_id,
            turn_id,
            kind="template",
            body=None,
            meta={"kind": "human_template", "template": tpl.name, "language": language},
            admin_id=admin.id,
        )
        await enqueue(
            redis,
            Queue.SEND,
            {
                "kind": "template",
                "to": to,
                "turn_id": turn_id,
                "name": tpl.name,
                "language": language,
            },
            job_id=f"send:{turn_id}",
        )
        if fresh:
            await take_case(admin, row)
            await store.audit(
                actor(admin),
                "human_template",
                user_id,
                {"escalation": escalation_id, "turn_id": turn_id, "template": tpl.name},
            )
        return {"turn_id": turn_id, "queued": True}

    # --- users ----------------------------------------------------------------------

    @app.get("/api/users")
    async def users(
        admin: Admin,
        state: UserState | None = None,
        phone: str | None = None,
        before: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[dict[str, Any]]:
        wa_hash = None
        if phone:
            wa_id = normalize_phone(phone)
            if wa_id is None:
                raise HTTPException(422, "bad_phone_number")
            wa_hash = lookup_hash(lookup_key, wa_id)
            await store.audit(actor(admin), "search_phone")  # the number itself is not kept
        rows = await store.list_users(state=state, wa_hash=wa_hash, before=before, limit=limit)
        t = now()
        return [_user_json(r, t) for r in rows]

    async def _record(user_id: str) -> Any:
        try:
            uuid.UUID(user_id)
        except ValueError as e:
            raise HTTPException(404, "not_found") from e
        record = await store.user_record(user_id)
        if record is None:
            raise HTTPException(404, "not_found")
        return record

    @app.get("/api/users/{user_id}")
    async def user(user_id: str, admin: Admin) -> dict[str, Any]:
        record = await _record(user_id)
        await audit_view(admin, "view_user", user_id)
        t = now()
        birth = record.birth
        return {
            "user": _user_json(record.row, t),
            # Masked: whether we hold them, never the values (see /reveal).
            "birth": None
            if birth is None
            else {
                "on_file": True,
                "name_on_file": birth.name_enc is not None,
                "time_known": birth.time_known,
                "tz_name": birth.tz_name,
            },
            "consents": [asdict(c) for c in record.consents],
            "escalations": [_escalation_json(e, t) for e in record.escalations],
            "ledger": [asdict(e) for e in record.ledger],
            "facts": [asdict(f) for f in await store.facts(user_id, 100)],
            "readings": [asdict(r) for r in await store.readings(user_id, 100)],
        }

    @app.get("/api/users/{user_id}/messages")
    async def messages(
        user_id: str,
        admin: Admin,
        before: int | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[dict[str, Any]]:
        await _record(user_id)
        await audit_view(admin, "view_messages", user_id)
        page = await store.messages_page(user_id, before_id=before, limit=limit)
        return [asdict(m) for m in page]

    @app.post("/api/users/{user_id}/reveal")
    async def reveal(user_id: str, admin: Owner) -> dict[str, Any]:
        record = await _record(user_id)
        await store.audit(actor(admin), "reveal_personal_data", user_id)
        b = record.birth

        def dec(field: str, blob: bytes | None) -> str | None:
            return None if blob is None else cipher.decrypt(field, blob, user_id)

        blob = await store.wa_id_enc(user_id)
        return {
            "phone": dec("wa_id", blob),
            "name": dec("name", b.name_enc) if b else None,
            "birth_date": dec("birth_date", b.date_enc) if b else None,
            "birth_time": dec("birth_time", b.time_enc) if b else None,
            "place": dec("place_label", b.place_enc) if b else None,
            "latitude": dec("latitude", b.latitude_enc) if b else None,
            "longitude": dec("longitude", b.longitude_enc) if b else None,
        }

    async def _set_blocked(user_id: str, admin: AdminUser, blocked: bool) -> dict[str, Any]:
        await _record(user_id)
        state = await store.set_blocked(user_id, blocked)
        if state is None:
            raise HTTPException(409, "not_allowed_in_current_state")
        await store.audit(actor(admin), "block" if blocked else "unblock", user_id)
        return {"state": state}

    @app.post("/api/users/{user_id}/block")
    async def block(user_id: str, admin: Owner) -> dict[str, Any]:
        return await _set_blocked(user_id, admin, True)

    @app.post("/api/users/{user_id}/unblock")
    async def unblock(user_id: str, admin: Owner) -> dict[str, Any]:
        return await _set_blocked(user_id, admin, False)

    @app.post("/api/users/{user_id}/credits")
    async def credits(user_id: str, body: CreditsIn, admin: Owner) -> dict[str, Any]:
        await _record(user_id)
        if body.delta == 0:
            raise HTTPException(422, "delta_must_not_be_zero")
        fresh = await store.add_credits(
            user_id,
            body.delta,
            "adjust",
            f"adjust:{body.client_id}",
            {"by": actor(admin), "note": body.note},
        )
        if fresh:
            await store.audit(actor(admin), "credit_adjust", user_id, {"delta": body.delta})
        record = await _record(user_id)
        return {"balance": record.row.balance, "applied": fresh}

    # --- metrics, config, audit ----------------------------------------------------------

    @app.get("/api/metrics")
    async def metrics(
        admin: Admin, days: Annotated[int, Query(ge=1, le=90)] = 14
    ) -> dict[str, Any]:
        return asdict(await store.metrics(now(), days))

    @app.get("/api/config")
    async def config(admin: Admin) -> list[dict[str, Any]]:
        entries = {e.key: e for e in await store.config_entries()}
        out = []
        for key in sorted(appconfig.SCHEMAS):
            e = entries.get(key)
            out.append(
                {
                    "key": key,
                    "value": e.value if e else appconfig.DEFAULTS[key],
                    "updated_at": e.updated_at if e else None,
                    "updated_by": e.updated_by if e else None,
                }
            )
        return out

    @app.put("/api/config/{key}")
    async def set_config(key: str, body: ConfigIn, admin: Owner) -> dict[str, Any]:
        try:
            value = appconfig.validate(key, body.value)
        except appconfig.ConfigError as e:
            raise HTTPException(422, str(e)) from e
        before = await store.get_config(key)
        await store.set_config(key, value, actor(admin))
        await store.audit(
            actor(admin), "config_update", None, {"key": key, "before": before, "after": value}
        )
        log.info("config %s updated by %s", key, actor(admin))
        return {"key": key, "value": value}

    @app.get("/api/audit")
    async def audit(
        admin: Owner,
        user_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        if user_id is not None:
            await _record(user_id)
        entries = await store.audit_entries(subject_user_id=user_id, limit=limit)
        return [asdict(a) for a in entries]

    return app
