"""Refunds and disputes: money that goes back to the user after an order was credited.

Jobs on the `payment` queue, queued by the Razorpay webhook (which only says "look"):
  {"kind": "refund", "payment_id": ...}    a refund was processed on a payment
  {"kind": "dispute", "dispute_id": ...}   a chargeback was opened or decided

Like payments, nothing is trusted from the webhook: the payment or dispute is fetched
from Razorpay first. Policy (decided 2026-09-28):
- A payment refunded in full (from the dashboard, or a dispute the bank decided against
  us) takes back the pack's unused credits, never below a zero balance, or ends the pass.
  Credits already spent are not clawed back. The user gets a short note.
- A partial refund, a dispute, or a second payment for one order is not acted on: the
  team gets a notice and settles it in the Razorpay dashboard (console → Payments).
"""

import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from redis.asyncio import Redis

from guruji.agent.language import Language
from guruji.alerts.worker import notify_team
from guruji.billing import catalog
from guruji.billing.razorpay import RazorpayError
from guruji.config import Settings
from guruji.crypto import FieldCipher, decode_key
from guruji.db.models import Order
from guruji.db.store import Store
from guruji.queue.streams import PermanentJobError, Queue, enqueue

log = logging.getLogger(__name__)


class Lookup(Protocol):
    async def payment(self, payment_id: str) -> dict[str, Any]: ...
    async def dispute(self, dispute_id: str) -> dict[str, Any]: ...


def rupees(paise: int) -> str:
    return f"₹{paise / 100:.2f}".removesuffix(".00")


class RefundHandler:
    def __init__(self, redis: Redis, settings: Settings, store: Store, razorpay: Lookup) -> None:
        self.redis = redis
        self.store = store
        self.razorpay = razorpay
        self.cipher = FieldCipher(decode_key(settings.field_encryption_key))

    async def __call__(self, queue: Queue, job: dict[str, Any]) -> None:
        kind = job.get("kind")
        try:
            if kind == "refund":
                await self._refund(str(job.get("payment_id", "")))
            elif kind == "dispute":
                await self._dispute(str(job.get("dispute_id", "")))
            else:
                raise PermanentJobError(f"unknown refund job kind {kind!r}")
        except RazorpayError as e:
            if e.retryable:
                raise
            raise PermanentJobError(str(e)) from e

    async def _refund(self, payment_id: str) -> None:
        pay = await self.razorpay.payment(payment_id)
        amount = int(pay.get("amount") or 0)
        refunded = int(pay.get("amount_refunded") or 0)
        if refunded <= 0:
            return  # nothing refunded (yet)
        order = await self.store.order_by_payment(payment_id)
        if order is None:
            # Not the payment that bought something, e.g. a double payment the team
            # refunded: nothing to take back.
            log.info("refund on payment %s with no order", payment_id)
            return
        if refunded < amount:
            fresh = await self.store.record_payment_issue(
                f"partial_refund:{payment_id}:{refunded}",
                "partial_refund",
                payment_id,
                reference_id=order.reference_id,
                amount_paise=refunded,
                details={"amount_paise": amount, "refunded_paise": refunded},
            )
            if fresh:
                await notify_team(
                    self.redis,
                    f"partial_refund:{payment_id}:{refunded}",
                    f"Partial refund of {rupees(refunded)} of {rupees(amount)} on order "
                    f"{order.reference_id}. Credits unchanged: settle it by hand "
                    "(console → Payments).",
                )
            return
        await self._claw_back(order)

    async def _dispute(self, dispute_id: str) -> None:
        d = await self.razorpay.dispute(dispute_id)
        payment_id = str(d.get("payment_id") or "")
        if not payment_id:
            raise PermanentJobError(f"dispute {dispute_id} has no payment")
        status = str(d.get("status") or "open")
        amount = int(d.get("amount") or 0)
        order = await self.store.order_by_payment(payment_id)
        ref = order.reference_id if order else None
        key = f"dispute:{dispute_id}:{status}"
        fresh = await self.store.record_payment_issue(
            key,
            "dispute",
            payment_id,
            reference_id=ref,
            amount_paise=amount,
            details={"dispute_id": dispute_id, "status": status, "reason": d.get("reason_code")},
        )
        if fresh:
            await notify_team(
                self.redis,
                key,
                f"Payment dispute ({status}) on order {ref or 'unknown'}, {rupees(amount)}, "
                f"dispute {dispute_id}. Answer it in the Razorpay dashboard.",
            )
        if status == "lost" and order is not None:  # the bank took the money back
            await self._claw_back(order)

    async def _claw_back(self, order: Order) -> None:
        result = await self.store.claw_back_order(order.reference_id, datetime.now(UTC))
        if result is None:
            return  # already done (a second webhook for the same refund)
        taken, ended = result
        log.info(
            "order %s refunded: %d credits taken back, pass ended=%s",
            order.reference_id,
            taken,
            ended,
        )
        await self._tell(order, taken, ended)

    async def _tell(self, order: Order, taken: int, ended: bool) -> None:
        record = await self.store.user_record(order.user_id)
        blob = await self.store.wa_id_enc(order.user_id)
        if record is None or blob is None or record.row.state == "opted_out":
            return  # erased, or after STOP: nothing else gets a reply
        lang: Language = (
            record.row.language if record.row.language in ("en", "hi", "hinglish") else "en"  # type: ignore[assignment]
        )
        price = rupees(order.amount_paise)
        if taken:
            line = catalog.text("refunded_pack", lang, price=price, n=taken)
        elif ended:
            line = catalog.text("refunded_pass", lang, price=price)
        else:
            line = catalog.text("refunded", lang, price=price)
        to = self.cipher.decrypt("wa_id", blob, order.user_id)
        turn_id = f"refund_{order.reference_id}"
        await enqueue(
            self.redis,
            Queue.SEND,
            {"kind": "bubbles", "to": to, "turn_id": turn_id, "bubbles": [line], "buttons": []},
            job_id=f"send:{turn_id}",
        )
