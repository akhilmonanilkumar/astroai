"""Payment worker: confirm with Razorpay, credit exactly once, tell the user.

Jobs on the `payment` queue: {"kind": "check", "reference_id": ...}, queued by the
WhatsApp payment-status webhook, the Razorpay webhook, or both (duplicates are harmless:
`fulfil_order` credits only a pending order). A payment still pending is re-checked with
growing delays, `payment_check_attempts` times.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from redis.asyncio import Redis

from guruji.agent.language import Language
from guruji.billing import catalog
from guruji.billing.razorpay import PaymentState, RazorpayError
from guruji.config import Settings
from guruji.crypto import FieldCipher, decode_key
from guruji.db.models import Order
from guruji.db.store import Store
from guruji.queue.streams import PermanentJobError, Queue, RetryJob, enqueue

log = logging.getLogger(__name__)


class Checker(Protocol):
    async def check(self, reference_id: str, amount_paise: int) -> PaymentState: ...


def _lang(value: str | None) -> Language:
    return value if value in ("en", "hi", "hinglish") else "en"  # type: ignore[return-value]


class PaymentHandler:
    def __init__(self, redis: Redis, settings: Settings, store: Store, checker: Checker) -> None:
        self.redis = redis
        self.settings = settings
        self.store = store
        self.checker = checker
        self.cipher = FieldCipher(decode_key(settings.field_encryption_key))

    async def __call__(self, queue: Queue, job: dict[str, Any]) -> None:
        if job.get("kind") != "check":
            raise PermanentJobError(f"unknown payment job kind {job.get('kind')!r}")
        ref = str(job.get("reference_id", ""))
        order = await self.store.get_order(ref)
        if order is None:
            log.warning("payment check for an unknown order %s", ref)
            return
        if order.status == "paid":
            return  # already credited; a duplicate webhook
        try:
            state = await self.checker.check(ref, order.amount_paise)
        except RazorpayError as e:
            if e.retryable:
                raise
            raise PermanentJobError(str(e)) from e
        if state.status == "paid" and state.payment_id:
            paid = await self.store.fulfil_order(ref, state.payment_id)
            if paid is not None:
                if order.status != "pending":  # paid after we gave up on it: still credited
                    log.warning("order %s paid late (was %s), credited now", ref, order.status)
                log.info("order %s paid (%s %s)", ref, paid.kind, paid.item_id)
                await self._tell(paid, "paid")
                await enqueue(
                    self.redis,
                    Queue.BACKGROUND,
                    {
                        "kind": "capi",
                        "event": "Purchase",
                        "user_id": paid.user_id,
                        "value": paid.amount_paise / 100,
                        "order": ref,
                    },
                    job_id=f"capi:purchase:{ref}",
                )
            return
        if order.status != "pending":
            return  # a reconciliation check of a failed or expired order: nothing new
        if state.status == "failed":
            if await self.store.fail_order(ref):
                log.info("order %s failed", ref)
                await self._tell(order, "failed")
            return
        tries = await self.redis.incr(f"paycheck:{ref}")
        await self.redis.expire(f"paycheck:{ref}", 3 * 24 * 3600)
        if tries >= self.settings.payment_check_attempts:
            log.warning("order %s still pending after %d checks", ref, tries)
            return
        raise RetryJob(delay=min(10.0 * 2 ** (tries - 1), 600.0))

    async def _tell(self, order: Order, outcome: str) -> None:
        record = await self.store.user_record(order.user_id)
        blob = await self.store.wa_id_enc(order.user_id)
        if record is None or blob is None:
            return
        if record.row.state == "opted_out":
            return  # after STOP nothing else gets a reply; the credit is kept all the same
        lang = _lang(record.row.language)
        if outcome == "failed":
            line = catalog.text("failed", lang)
        elif order.kind == "pack":
            line = catalog.text("paid_pack", lang, n=order.prashnas, balance=record.row.balance)
        else:
            plan = await self.store.active_pass(order.user_id, datetime.now(UTC))
            until = f"{plan.ends_at:%d %b %Y}" if plan else ""
            line = catalog.text("paid_pass", lang, until=until)
        to = self.cipher.decrypt("wa_id", blob, order.user_id)
        turn_id = f"pay_{order.reference_id}_{outcome}"
        await enqueue(
            self.redis,
            Queue.SEND,
            {"kind": "bubbles", "to": to, "turn_id": turn_id, "bubbles": [line], "buttons": []},
            job_id=f"send:{turn_id}",
        )


async def reconcile_orders(redis: Redis, store: Store, settings: Settings) -> int:
    """Queue a Razorpay check for every recent unpaid order, so a payment whose webhook
    never arrived (or that came after polling stopped, or after the order expired) is
    still credited. Returns how many checks were queued."""
    since = datetime.now(UTC) - timedelta(days=settings.payment_reconcile_days)
    refs = await store.orders_to_reconcile(since, settings.payment_reconcile_batch)
    hour = datetime.now(UTC).strftime("%Y%m%d%H")
    for ref in refs:
        await enqueue(
            redis,
            Queue.PAYMENT,
            {"kind": "check", "reference_id": ref},
            job_id=f"paycheck:recon:{ref}:{hour}",
        )
    return len(refs)


async def run_reconcile(
    redis: Redis, store: Store, settings: Settings, stop: asyncio.Event
) -> None:
    """Every `payment_reconcile_seconds`, one replica re-checks recent unpaid orders."""
    every = settings.payment_reconcile_seconds
    while not stop.is_set():
        try:
            if await redis.set("reconcile:run", "1", nx=True, ex=max(1, int(every) - 60)):
                n = await reconcile_orders(redis, store, settings)
                if n:
                    log.info("payment reconciliation queued %d checks", n)
        except Exception:
            log.exception("payment reconciliation failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=every)
