"""Background jobs (the `background` queue), run by the `jobs` role with payment checks.

{"kind": "capi", "event": "Lead" | "Purchase", "user_id" | "wa_id", "value"?, "order"?}
    Meta Conversions API event for a user who came from a Click-to-WhatsApp ad, so the
    ads report shows ad -> onboarded -> paid. Skipped when CAPI is not configured or the
    user has no ad click id. No personal data is sent: the click id and the WhatsApp
    business account identify the conversation to Meta.
"""

import logging
import time
from typing import Any, Protocol

import httpx

from guruji.config import Settings
from guruji.crypto import decode_key, lookup_hash
from guruji.db.store import Store
from guruji.queue.streams import PermanentJobError, Queue

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


class BackgroundHandler:
    def __init__(self, settings: Settings, store: Store, capi: Capi | None) -> None:
        self.settings = settings
        self.store = store
        self.capi = capi
        self.lookup_key = decode_key(settings.lookup_hmac_key)

    async def __call__(self, queue: Queue, job: dict[str, Any]) -> None:
        kind = job.get("kind")
        if kind == "capi":
            await self._capi(job)
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
