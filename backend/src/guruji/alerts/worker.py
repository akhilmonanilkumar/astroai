"""Team alerts for escalations: Telegram in staging/prod, the log in dev.

Alerts carry IDs and categories only, never names, phone numbers, birth data or message
text (CLAUDE.md). Crisis-level escalations (severity 1) that nobody acknowledges are
re-sent every `alert_repeat_seconds` until someone taps "Acknowledge".

Jobs on the `alert` queue:
  {"kind": "escalation", "escalation_id": ...}   send (or re-send) the alert
  {"kind": "ack", "escalation_id": ..., "by": "telegram:<id>", "callback_id": ...}
"""

import asyncio
import contextlib
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from redis.asyncio import Redis

from guruji.config import Settings
from guruji.db.models import Escalation
from guruji.db.store import Store
from guruji.queue.streams import PermanentJobError, Queue, enqueue

log = logging.getLogger(__name__)

_SEVERITY_LABEL = {1: "URGENT", 2: "high", 3: "normal"}


def escalation_tag(esc: Escalation) -> str:
    """Short stable tag for the user, like logs.user_tag but from the internal user id."""
    return "u_" + hashlib.sha256(esc.user_id.encode()).hexdigest()[:10]


def alert_text(esc: Escalation, console_url: str) -> str:
    repeat = f" (reminder {esc.alert_count})" if esc.alert_count else ""
    return (
        f"[{_SEVERITY_LABEL.get(esc.severity, esc.severity)}] Guruji escalation{repeat}\n"
        f"category: {esc.category}\n"
        f"user: {escalation_tag(esc)}\n"
        f"opened: {esc.opened_at:%Y-%m-%d %H:%M} UTC\n"
        f"id: {esc.id}\n"
        f"{console_url.rstrip('/')}/escalations/{esc.id}"
    )


class Alerter(Protocol):
    async def send(self, esc: Escalation) -> None: ...
    async def acknowledged(self, callback_id: str | None) -> None: ...


class LogAlerter:
    """Dev/test: alerts go to the log (no personal data there either)."""

    def __init__(self, console_url: str) -> None:
        self.console_url = console_url
        self.sent: list[str] = []

    async def send(self, esc: Escalation) -> None:
        text = alert_text(esc, self.console_url)
        self.sent.append(text)
        log.warning("ALERT %s", text.replace("\n", " | "))

    async def acknowledged(self, callback_id: str | None) -> None:
        return None


class TelegramAlerter:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        console_url: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._api = f"https://api.telegram.org/bot{bot_token}"
        self._chat = chat_id
        self.console_url = console_url
        self._http = http or httpx.AsyncClient(timeout=15.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, payload: dict[str, Any]) -> None:
        try:
            r = await self._http.post(f"{self._api}/{method}", json=payload)
        except httpx.TransportError as e:
            raise RuntimeError(f"telegram {method}: {type(e).__name__}") from e
        if r.status_code >= 500 or r.status_code == 429:
            raise RuntimeError(f"telegram {method}: {r.status_code}")  # retried by the worker
        if r.status_code >= 400:
            raise PermanentJobError(f"telegram {method}: {r.status_code}")

    async def send(self, esc: Escalation) -> None:
        keyboard = [[{"text": "Acknowledge", "callback_data": f"ack:{esc.id}"}]]
        await self._call(
            "sendMessage",
            {
                "chat_id": self._chat,
                "text": alert_text(esc, self.console_url),
                "reply_markup": {"inline_keyboard": keyboard},
                "disable_web_page_preview": True,
            },
        )

    async def acknowledged(self, callback_id: str | None) -> None:
        if callback_id:
            await self._call(
                "answerCallbackQuery", {"callback_query_id": callback_id, "text": "Acknowledged"}
            )


class AlertHandler:
    def __init__(self, store: Store, alerter: Alerter) -> None:
        self.store = store
        self.alerter = alerter

    async def __call__(self, queue: Queue, job: dict[str, Any]) -> None:
        kind = job.get("kind")
        esc_id = str(job.get("escalation_id", ""))
        if kind == "escalation":
            esc = await self.store.get_escalation(esc_id)
            if esc is None or esc.status not in ("open", "acknowledged"):
                return  # resolved meanwhile: nothing to say
            await self.alerter.send(esc)
            await self.store.mark_alerted(esc_id)
        elif kind == "ack":
            acked = await self.store.acknowledge_escalation(esc_id, str(job.get("by", "")))
            log.info("escalation %s acknowledged=%s", esc_id, acked)
            await self.alerter.acknowledged(job.get("callback_id"))
        else:
            raise PermanentJobError(f"unknown alert job kind {kind!r}")


async def reping_due(redis: Redis, store: Store, settings: Settings) -> int:
    """Queue reminders for unacknowledged urgent escalations. Returns how many."""
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.alert_repeat_seconds)
    due = await store.escalations_to_reping(cutoff)
    for esc in due:
        await enqueue(
            redis,
            Queue.ALERT,
            {"kind": "escalation", "escalation_id": esc.id},
            job_id=f"alert:{esc.id}:reping:{esc.alert_count}",
        )
    return len(due)


async def run_repinger(redis: Redis, store: Store, settings: Settings, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            n = await reping_due(redis, store, settings)
            if n:
                log.warning("re-alerting %d unacknowledged escalations", n)
        except Exception:
            log.exception("re-ping check failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.alert_check_seconds)
