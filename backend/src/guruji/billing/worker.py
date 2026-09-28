"""Payment worker: confirm with Razorpay, credit exactly once, tell the user.

Jobs on the `payment` queue: {"kind": "check", "reference_id": ...}, queued by the
WhatsApp payment-status webhook, the Razorpay webhook, or both (duplicates are harmless:
`fulfil_order` credits only a pending order). A payment still pending is re-checked with
growing delays, `payment_check_attempts` times.
"""

import logging
from datetime import UTC, datetime
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
        if order.status != "pending":
            return  # already settled; a duplicate webhook
        try:
            state = await self.checker.check(ref, order.amount_paise)
        except RazorpayError as e:
            if e.retryable:
                raise
            raise PermanentJobError(str(e)) from e
        if state.status == "paid" and state.payment_id:
            paid = await self.store.fulfil_order(ref, state.payment_id)
            if paid is not None:
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
