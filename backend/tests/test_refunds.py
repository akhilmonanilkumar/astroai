"""PR-10: refunds take back unused credits or end a pass; disputes, double payments and
partial refunds go to the team without touching the user's chat."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from guruji.billing.razorpay import PaymentState
from guruji.billing.refunds import RefundHandler
from guruji.billing.worker import PaymentHandler
from guruji.config import Settings
from guruji.crypto import FieldCipher, decode_key
from guruji.db.models import Order, TurnWrite
from guruji.db.store import MemoryStore
from guruji.ingress.app import create_app
from guruji.queue.streams import Queue

WA = "919800000001"


def signed_razorpay(event: dict[str, Any], secret: str) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(event).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Razorpay-Signature": sig}


class FakeRazorpay:
    def __init__(
        self, payments: dict[str, dict[str, Any]], disputes: dict[str, dict[str, Any]] | None = None
    ) -> None:
        self.payments = payments
        self.disputes = disputes or {}

    async def payment(self, payment_id: str) -> dict[str, Any]:
        return self.payments[payment_id]

    async def dispute(self, dispute_id: str) -> dict[str, Any]:
        return self.disputes[dispute_id]


class FakeChecker:
    def __init__(self, state: PaymentState) -> None:
        self.state = state

    async def check(self, reference_id: str, amount_paise: int) -> PaymentState:
        return self.state


async def _jobs(redis: Any, queue: str) -> list[dict[str, Any]]:
    return [json.loads(f["job"]) for _, f in await redis.xrange(f"q:{queue}")]


async def _paid_user(store: MemoryStore, settings: Settings, order: Order, payment_id: str) -> str:
    user, _ = await store.get_or_create_user("h-refund")
    cipher = FieldCipher(decode_key(settings.field_encryption_key))
    await store.set_wa_id(user.id, cipher.encrypt("wa_id", WA, user.id))
    await store.create_order(
        Order(
            order.reference_id,
            user.id,
            order.kind,
            order.item_id,
            order.amount_paise,
            prashnas=order.prashnas,
            days=order.days,
        )
    )
    assert await store.fulfil_order(order.reference_id, payment_id) is not None
    return user.id


async def test_full_refund_takes_back_only_unused_credits(redis: Any, settings: Settings) -> None:
    store = MemoryStore()
    uid = await _paid_user(
        store, settings, Order("gj1", "", "pack", "p51", 5100, prashnas=10), "pay_1"
    )
    await store.add_credits(uid, -7, "spend", "spend:t1")  # 7 of the 10 already used
    rzp = FakeRazorpay({"pay_1": {"amount": 5100, "amount_refunded": 5100}})
    handler = RefundHandler(redis, settings, store, rzp)
    await handler(Queue.PAYMENT, {"kind": "refund", "payment_id": "pay_1"})
    await handler(Queue.PAYMENT, {"kind": "refund", "payment_id": "pay_1"})  # repeat webhook
    assert await store.balance(uid) == 0  # 3 taken back, never below zero
    order = await store.get_order("gj1")
    assert order is not None and order.status == "refunded"
    [send] = await _jobs(redis, "send")
    assert send["to"] == WA and "3 unused credits" in send["bubbles"][0]
    assert await store.list_payment_issues(open_only=True) == []


async def test_refunded_order_is_not_credited_again(redis: Any, settings: Settings) -> None:
    store = MemoryStore()
    await _paid_user(store, settings, Order("gj2", "", "pack", "p51", 5100, prashnas=10), "pay_2")
    await RefundHandler(
        redis, settings, store, FakeRazorpay({"pay_2": {"amount": 5100, "amount_refunded": 5100}})
    )(Queue.PAYMENT, {"kind": "refund", "payment_id": "pay_2"})
    assert await store.fulfil_order("gj2", "pay_2") is None
    since = datetime.now(UTC) - timedelta(days=1)
    assert "gj2" not in await store.orders_to_reconcile(since, 10)


async def test_refunded_pass_ends(redis: Any, settings: Settings) -> None:
    store = MemoryStore()
    uid = await _paid_user(
        store, settings, Order("gj3", "", "pass", "plus_monthly", 19900, days=30), "pay_3"
    )
    assert await store.active_pass(uid, datetime.now(UTC)) is not None
    rzp = FakeRazorpay({"pay_3": {"amount": 19900, "amount_refunded": 19900}})
    await RefundHandler(redis, settings, store, rzp)(
        Queue.PAYMENT, {"kind": "refund", "payment_id": "pay_3"}
    )
    assert await store.active_pass(uid, datetime.now(UTC) + timedelta(seconds=1)) is None
    [send] = await _jobs(redis, "send")
    assert "Guru Plus" in send["bubbles"][0]


async def test_partial_refund_goes_to_the_team(redis: Any, settings: Settings) -> None:
    store = MemoryStore()
    uid = await _paid_user(
        store, settings, Order("gj4", "", "pack", "p51", 5100, prashnas=10), "pay_4"
    )
    rzp = FakeRazorpay({"pay_4": {"amount": 5100, "amount_refunded": 2000}})
    await RefundHandler(redis, settings, store, rzp)(
        Queue.PAYMENT, {"kind": "refund", "payment_id": "pay_4"}
    )
    assert await store.balance(uid) == 10
    [issue] = await store.list_payment_issues(open_only=True)
    assert (issue.kind, issue.reference_id, issue.user_id) == ("partial_refund", "gj4", uid)
    [notice] = await _jobs(redis, "alert")
    assert notice["kind"] == "notice" and "gj4" in notice["text"] and WA not in notice["text"]
    assert await _jobs(redis, "send") == []


async def test_dispute_alerts_and_a_lost_one_claws_back(redis: Any, settings: Settings) -> None:
    store = MemoryStore()
    uid = await _paid_user(
        store, settings, Order("gj5", "", "pack", "p51", 5100, prashnas=10), "pay_5"
    )
    rzp = FakeRazorpay({}, {"disp_1": {"payment_id": "pay_5", "amount": 5100, "status": "open"}})
    handler = RefundHandler(redis, settings, store, rzp)
    await handler(Queue.PAYMENT, {"kind": "dispute", "dispute_id": "disp_1"})
    assert await store.balance(uid) == 10  # an open dispute changes nothing for the user
    assert [i.kind for i in await store.list_payment_issues(open_only=True)] == ["dispute"]
    assert len(await _jobs(redis, "alert")) == 1

    rzp.disputes["disp_1"]["status"] = "lost"
    await handler(Queue.PAYMENT, {"kind": "dispute", "dispute_id": "disp_1"})
    assert await store.balance(uid) == 0
    assert len(await _jobs(redis, "alert")) == 2


async def test_double_payment_is_flagged_once(redis: Any, settings: Settings) -> None:
    store = MemoryStore()
    user, _ = await store.get_or_create_user("h-dup")
    await store.create_order(Order("gj6", user.id, "pack", "p51", 5100, prashnas=10))
    state = PaymentState("paid", "pay_6", 10200, ("pay_7",))
    handler = PaymentHandler(redis, settings, store, FakeChecker(state))
    await handler(Queue.PAYMENT, {"kind": "check", "reference_id": "gj6"})
    await handler(Queue.PAYMENT, {"kind": "check", "reference_id": "gj6"})
    assert await store.balance(user.id) == 10  # credited once
    [issue] = await store.list_payment_issues(open_only=True)
    assert (issue.kind, issue.payment_id) == ("duplicate", "pay_7")
    assert await store.resolve_payment_issue(issue.id, "admin:x")
    assert not await store.resolve_payment_issue(issue.id, "admin:x")
    assert await store.list_payment_issues(open_only=True) == []


async def test_no_refund_note_after_stop(redis: Any, settings: Settings) -> None:
    store = MemoryStore()
    uid = await _paid_user(
        store, settings, Order("gj8", "", "pack", "p51", 5100, prashnas=10), "pay_8"
    )
    await store.commit_turn(TurnWrite(uid, "t_stop", [], state="opted_out", opted_out=True))
    rzp = FakeRazorpay({"pay_8": {"amount": 5100, "amount_refunded": 5100}})
    await RefundHandler(redis, settings, store, rzp)(
        Queue.PAYMENT, {"kind": "refund", "payment_id": "pay_8"}
    )
    assert await store.balance(uid) == 0
    assert await _jobs(redis, "send") == []


async def test_webhook_routes_refunds_and_disputes(redis: Any, settings: Settings) -> None:
    app = create_app(settings, redis)
    secret = settings.razorpay_webhook_secret.get_secret_value()
    events = [
        {
            "event": "refund.processed",
            "payload": {"refund": {"entity": {"id": "rfnd_1", "payment_id": "pay_9"}}},
        },
        {"event": "payment.dispute.created", "payload": {"dispute": {"entity": {"id": "disp_9"}}}},
        {"event": "payment.dispute.lost", "payload": {"dispute": {"entity": {"id": "bad id!"}}}},
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://ingress"
    ) as c:
        for e in events:
            raw, headers = signed_razorpay(e, secret)
            assert (
                await c.post("/razorpay/webhook", content=raw, headers=headers)
            ).status_code == 200
    assert await _jobs(redis, "payment") == [
        {"kind": "refund", "payment_id": "pay_9", "_id": "refund:pay_9:rfnd_1"},
        {
            "kind": "dispute",
            "dispute_id": "disp_9",
            "_id": "dispute:disp_9:payment.dispute.created",
        },
    ]
