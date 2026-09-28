"""Background jobs (the `background` queue), run by the `jobs` role with payment checks.

{"kind": "capi", "event": "Lead" | "Purchase", "user_id" | "wa_id", "value"?, "order"?}
    Meta Conversions API event for a user who came from a Click-to-WhatsApp ad, so the
    ads report shows ad -> onboarded -> paid. Skipped when CAPI is not configured or the
    user has no ad click id. No personal data is sent: the click id and the WhatsApp
    business account identify the conversation to Meta.
"""

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from redis.asyncio import Redis

from guruji.agent.privacy import PRIVACY
from guruji.appconfig import ConfigReader
from guruji.config import Settings
from guruji.crypto import FieldCipher, decode_key, lookup_hash
from guruji.db.store import Store
from guruji.queue.streams import PermanentJobError, Queue
from guruji.whatsapp.client import WhatsAppClient, WhatsAppError

_EXPORT_MESSAGES = 5000
_BIRTH_FIELDS = (
    ("name", "name_enc"),
    ("birth_date", "date_enc"),
    ("birth_time", "time_enc"),
    ("place_label", "place_enc"),
    ("latitude", "latitude_enc"),
    ("longitude", "longitude_enc"),
)

log = logging.getLogger(__name__)


class Capi(Protocol):
    async def send(
        self, event: str, ctwa_clid: str, value: float | None, event_id: str
    ) -> None: ...


class MetaCapi:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        assert settings.capi_dataset_id and settings.capi_access_token
        base = "https://graph.facebook.com"
        self._url = f"{base}/{settings.graph_api_version}/{settings.capi_dataset_id}/events"
        self._token = settings.capi_access_token.get_secret_value()
        self._waba = settings.wa_business_account_id
        self._http = http or httpx.AsyncClient(timeout=15.0)

    async def send(self, event: str, ctwa_clid: str, value: float | None, event_id: str) -> None:
        data: dict[str, Any] = {
            "event_name": event,
            "event_time": int(time.time()),
            "event_id": event_id,  # Meta dedupes repeats of the same event
            "action_source": "business_messaging",
            "messaging_channel": "whatsapp",
            "user_data": {"whatsapp_business_account_id": self._waba, "ctwa_clid": ctwa_clid},
        }
        if value is not None:
            data["custom_data"] = {"currency": "INR", "value": value}
        try:
            r = await self._http.post(
                self._url, json={"data": [data]}, params={"access_token": self._token}
            )
        except httpx.TransportError as e:
            raise RuntimeError(f"capi: {type(e).__name__}") from e  # retried
        if r.status_code >= 500 or r.status_code == 429:
            raise RuntimeError(f"capi: {r.status_code}")
        if r.status_code >= 400:
            raise PermanentJobError(f"capi: {r.status_code} {r.text[:200]}")


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


async def export_data(store: Store, cipher: FieldCipher, user_id: str) -> dict[str, Any] | None:
    """Everything we hold about a user, decrypted, for "export my data"."""
    record = await store.user_record(user_id)
    if record is None:
        return None
    birth: dict[str, Any] | None = None
    if record.birth is not None:
        b = record.birth
        birth = {
            name: cipher.decrypt(name, getattr(b, attr), user_id)
            for name, attr in _BIRTH_FIELDS
            if getattr(b, attr) is not None
        }
        birth["time_known"], birth["timezone"] = b.time_known, b.tz_name
    blob = await store.wa_id_enc(user_id)
    messages: list[dict[str, Any]] = []
    before: int | None = None
    while len(messages) < _EXPORT_MESSAGES:
        page = await store.messages_page(user_id, before_id=before, limit=500)
        if not page:
            break
        messages = [
            {"at": m.created_at, "from": m.sent_by, "kind": m.kind, "text": m.body} for m in page
        ] + messages
        before = page[0].id
    return {
        "exported_at": datetime.now(UTC),
        "phone": cipher.decrypt("wa_id", blob, user_id) if blob else None,
        "language": record.row.language,
        "joined": record.row.created_at,
        "birth_details": birth,
        "consents": [asdict(c) for c in record.consents],
        "credits": {"balance": record.row.balance, "ledger": [asdict(e) for e in record.ledger]},
        "what_guruji_remembers": [asdict(f) for f in await store.facts(user_id, 1000)],
        "readings_given": [asdict(r) for r in await store.readings(user_id, 1000)],
        "messages": messages,
    }


class BackgroundHandler:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        capi: Capi | None,
        whatsapp: WhatsAppClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.capi = capi
        self.whatsapp = whatsapp
        self.lookup_key = decode_key(settings.lookup_hmac_key)
        self.cipher = FieldCipher(decode_key(settings.field_encryption_key))

    async def __call__(self, queue: Queue, job: dict[str, Any]) -> None:
        kind = job.get("kind")
        if kind == "capi":
            await self._capi(job)
        elif kind == "export":
            await self._export(job)
        else:
            raise PermanentJobError(f"unknown background job kind {kind!r}")

    async def _capi(self, job: dict[str, Any]) -> None:
        if self.capi is None:
            return  # not configured (dev)
        user_id = job.get("user_id")
        if not user_id and job.get("wa_id"):
            found = await self.store.list_users(
                wa_hash=lookup_hash(self.lookup_key, str(job["wa_id"])), limit=1
            )
            user_id = found[0].id if found else None
        if not user_id:
            return
        clid = await self.store.ctwa_clid(str(user_id))
        if clid is None:
            return  # not from a Click-to-WhatsApp ad
        event = str(job.get("event"))
        event_id = f"{event}:{job.get('order') or user_id}"
        value = job.get("value")
        await self.capi.send(event, clid, float(value) if value is not None else None, event_id)
        log.info("capi %s sent", event)

    async def _export(self, job: dict[str, Any]) -> None:
        if self.whatsapp is None:
            raise PermanentJobError("export needs a WhatsApp client")
        user_id = str(job.get("user_id"))
        data = await export_data(self.store, self.cipher, user_id)
        blob = await self.store.wa_id_enc(user_id)
        if data is None or blob is None:
            return  # erased meanwhile
        to = self.cipher.decrypt("wa_id", blob, user_id)
        lang = job.get("lang") if job.get("lang") in ("en", "hi", "hinglish") else "en"
        body = json.dumps(data, ensure_ascii=False, indent=1, default=_json).encode()
        try:
            media = await self.whatsapp.upload_media(body, "text/plain", "guruji-my-data.txt")
            await self.whatsapp.send_document(
                to,
                media,
                "guruji-my-data.txt",
                PRIVACY["export_caption"][lang],  # type: ignore[index]
            )
        except WhatsAppError as e:
            if e.retryable:
                raise
            raise PermanentJobError(str(e)) from e
        await self.store.audit("user", "export", user_id)
        log.info("data export sent (%d bytes)", len(body))


async def run_retention(
    redis: Redis, store: Store, stop: asyncio.Event, every_seconds: float = 3600.0
) -> None:
    """Hourly: erase long-opted-out users, drop old conversation text, expire orders.
    One replica per hour does it (a Redis key marks the hour as taken)."""
    config = ConfigReader(store)
    while not stop.is_set():
        try:
            if await redis.set("retention:run", "1", nx=True, ex=int(every_seconds) - 60):
                r = await config.get("retention")
                counts = await store.retention_sweep(
                    datetime.now(UTC),
                    opted_out_days=r.opted_out_days,
                    message_days=r.message_days,
                    pending_order_hours=r.pending_order_hours,
                )
                if any(counts.values()):
                    log.info("retention sweep %s", counts)
        except Exception:
            log.exception("retention sweep failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=every_seconds)
