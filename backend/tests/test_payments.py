"""Payments: offer and checkout messages, Razorpay checks, webhooks, crediting once."""

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest

from conftest import signed
from guruji.appconfig import DEFAULTS, SCHEMAS
from guruji.billing import catalog
from guruji.billing.razorpay import PaymentState, Razorpay, valid_webhook
from guruji.billing.worker import PaymentHandler
from guruji.config import Settings
from guruji.crypto import FieldCipher, decode_key
from guruji.db.models import Order
from guruji.db.store import MemoryStore
from guruji.ingress.app import create_app
from guruji.queue.streams import Queue, RetryJob

WA = "919800000001"
PACKS = SCHEMAS["packs"].validate_python(DEFAULTS["packs"])
PASSES = SCHEMAS["passes"].validate_python(DEFAULTS["passes"])


async def _jobs(redis: Any, queue: str) -> list[dict[str, Any]]:
    return [json.loads(f["job"]) for _, f in await redis.xrange(f"q:{queue}")]


# --- catalog ----------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "hinglish", "hi"])
def test_offer_fits_whatsapp_limits(lang: Any) -> None:
    card = catalog.offer(PACKS, PASSES, 5, lang, first_buy=True)
    rows = [r for s in card["action"]["sections"] for r in s["rows"]]
    assert 1 <= len(rows) <= 10 and len(card["action"]["button"]) <= 20
    assert all(len(r["title"]) <= 24 and len(r.get("description", "")) <= 72 for r in rows)
    assert all(len(s["title"]) <= 24 for s in card["action"]["sections"])
    assert "buy:trial" in {r["id"] for r in rows}
    later = catalog.offer(PACKS, PASSES, 5, lang, first_buy=False)
    assert "buy:trial" not in {r["id"] for s in later["action"]["sections"] for r in s["rows"]}


def test_checkout_card() -> None:
    item = catalog.items(PACKS, PASSES)["p101"]
    card = catalog.checkout(item, "gj123", "guruji-rzp", "en")
    params = card["action"]["parameters"]
    assert params["total_amount"] == {"value": 10100, "offset": 100}
    gateway = params["payment_settings"][0]["payment_gateway"]
    assert gateway["configuration_name"] == "guruji-rzp"
    assert gateway["razorpay"]["receipt"] == "gj123"


# --- Razorpay ---------------------------------------------------------------------------


def _rzp(orders: list[dict[str, Any]], payments: list[dict[str, Any]]) -> Razorpay:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        items = orders if request.url.path == "/v1/orders" else payments
        return httpx.Response(200, json={"items": items})

    return Razorpay(
        "k", "s", "https://rzp.test", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_razorpay_check() -> None:
    order = [{"id": "order_1", "receipt": "gj1"}]
    paid = await _rzp(order, [{"id": "pay_1", "status": "captured", "amount": 5100}]).check(
        "gj1", 5100
    )
    assert paid == PaymentState("paid", "pay_1", 5100)
    short = await _rzp(order, [{"id": "pay_1", "status": "captured", "amount": 100}]).check(
        "gj1", 5100
    )
    assert short.status == "pending"  # never credit a partial amount
    failed = await _rzp(order, [{"id": "p", "status": "failed"}]).check("gj1", 5100)
    assert failed.status == "failed"
    assert (await _rzp([], []).check("gj1", 5100)).status == "pending"


def test_webhook_signature() -> None:
    body = b'{"event":"order.paid"}'
    sig = hmac.new(b"sec", body, hashlib.sha256).hexdigest()
    assert valid_webhook(body, sig, "sec")
    assert not valid_webhook(body, sig, "other")
    assert not valid_webhook(body + b" ", sig, "sec")


# --- the payment worker -----------------------------------------------------------------


class FakeChecker:
    def __init__(self, state: PaymentState) -> None:
        self.state = state

    async def check(self, reference_id: str, amount_paise: int) -> PaymentState:
        return self.state


async def _user(store: MemoryStore, settings: Settings) -> str:
    user, _ = await store.get_or_create_user("h-pay")
    cipher = FieldCipher(decode_key(settings.field_encryption_key))
    await store.set_wa_id(user.id, cipher.encrypt("wa_id", WA, user.id))
    return user.id


async def test_paid_pack_is_credited_once_and_confirmed(redis: Any, settings: Settings) -> None:
    store = MemoryStore()
    uid = await _user(store, settings)
    await store.create_order(Order("gj1", uid, "pack", "p51", 5100, prashnas=10))
    handler = PaymentHandler(redis, settings, store, FakeChecker(PaymentState("paid", "pay_1")))
    job = {"kind": "check", "reference_id": "gj1"}
    await handler(Queue.PAYMENT, job)
    await handler(Queue.PAYMENT, job)  # the second webhook for the same payment
    assert await store.balance(uid) == 10
    [send] = await _jobs(redis, "send")
    assert send["to"] == WA and "10 credits" in send["bubbles"][0]
    [capi] = await _jobs(redis, "background")
    assert (capi["event"], capi["value"]) == ("Purchase", 51.0)


async def test_paid_pass_starts_guru_plus(redis: Any, settings: Settings) -> None:
    store = MemoryStore()
    uid = await _user(store, settings)
    await store.create_order(Order("gj2", uid, "pass", "plus_monthly", 19900, days=30))
    handler = PaymentHandler(redis, settings, store, FakeChecker(PaymentState("paid", "pay_2")))
    await handler(Queue.PAYMENT, {"kind": "check", "reference_id": "gj2"})
    from datetime import UTC, datetime

    assert await store.active_pass(uid, datetime.now(UTC)) is not None
    assert await store.balance(uid) == 0


async def test_pending_is_rechecked_and_failed_is_told(redis: Any, settings: Settings) -> None:
    store = MemoryStore()
    uid = await _user(store, settings)
    await store.create_order(Order("gj3", uid, "pack", "p51", 5100, prashnas=10))
    pending = PaymentHandler(redis, settings, store, FakeChecker(PaymentState("pending")))
    with pytest.raises(RetryJob):
        await pending(Queue.PAYMENT, {"kind": "check", "reference_id": "gj3"})
    failed = PaymentHandler(redis, settings, store, FakeChecker(PaymentState("failed")))
    await failed(Queue.PAYMENT, {"kind": "check", "reference_id": "gj3"})
    order = await store.get_order("gj3")
    assert order is not None and order.status == "failed"
    [send] = await _jobs(redis, "send")
    assert "didn't go through" in send["bubbles"][0]
    await failed(Queue.PAYMENT, {"kind": "check", "reference_id": "nope"})  # unknown: ignored


# --- ingress ----------------------------------------------------------------------------


async def test_webhooks_only_queue_a_check(redis: Any, settings: Settings) -> None:
    app = create_app(settings, redis)
    status = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "W",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "x",
                                    "type": "payment",
                                    "status": "captured",
                                    "payment": {"reference_id": "gj9"},
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    }
    raw, headers = signed(status, settings.wa_app_secret)
    rzp_body = json.dumps(
        {"event": "order.paid", "payload": {"order": {"entity": {"receipt": "gj8"}}}}
    ).encode()
    secret = settings.razorpay_webhook_secret.get_secret_value()
    good = hmac.new(secret.encode(), rzp_body, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://ingress"
    ) as c:
        assert (await c.post("/webhook", content=raw, headers=headers)).status_code == 200
        bad = await c.post(
            "/razorpay/webhook", content=rzp_body, headers={"X-Razorpay-Signature": "0" * 64}
        )
        assert bad.status_code == 401
        ok = await c.post(
            "/razorpay/webhook", content=rzp_body, headers={"X-Razorpay-Signature": good}
        )
        assert ok.status_code == 200
    refs = [j["reference_id"] for j in await _jobs(redis, "payment")]
    assert refs == ["gj9", "gj8"]


# --- simulator's Razorpay ---------------------------------------------------------------


async def test_simulator_registers_checkout_orders(settings: Settings) -> None:
    from guruji.simulator.app import create_app as create_sim

    sim = create_sim(settings)
    card = catalog.checkout(catalog.items(PACKS, PASSES)["p51"], "gj7", "x", "en")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://s") as c:
        r = await c.post(
            f"/{settings.graph_api_version}/{settings.wa_phone_number_id}/messages",
            json={"to": WA, "type": "interactive", "interactive": card},
            headers={"Authorization": f"Bearer {settings.wa_access_token}"},
        )
        assert r.status_code == 200
        auth = (settings.razorpay_key_id, settings.razorpay_key_secret.get_secret_value())
        orders = (await c.get("/razorpay/v1/orders", params={"receipt": "gj7"}, auth=auth)).json()
        assert orders["items"][0]["amount"] == 5100
        assert (await c.get("/razorpay/v1/orders", params={"receipt": "gj7"})).status_code == 401


# --- data export (DPDP) -----------------------------------------------------------------


async def test_export_is_sent_as_a_document(settings: Settings) -> None:
    from guruji.db.models import EncryptedBirth, InboundLog, TurnWrite
    from guruji.jobs import BackgroundHandler

    store = MemoryStore()
    uid = await _user(store, settings)
    cipher = FieldCipher(decode_key(settings.field_encryption_key))
    birth = EncryptedBirth(
        cipher.encrypt("name", "Priya", uid),
        cipher.encrypt("birth_date", "1990-07-15", uid),
        None,
        False,
        cipher.encrypt("place_label", "Pune", uid),
        cipher.encrypt("latitude", "18.5", uid),
        cipher.encrypt("longitude", "73.8", uid),
        "Asia/Kolkata",
    )
    await store.commit_turn(
        TurnWrite(
            uid,
            "t1",
            [InboundLog("w1", "text", "meri shaadi?")],
            reply_body="jald",
            birth=birth,
            state="active",
        )
    )
    calls: list[tuple[str, Any]] = []

    def graph(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/media"):
            calls.append(("upload", request.content))
            return httpx.Response(200, json={"id": "media-1"})
        calls.append(("send", json.loads(request.content)))
        return httpx.Response(200, json={"messages": [{"id": "wamid.D"}]})

    from guruji.whatsapp.client import WhatsAppClient

    wa = WhatsAppClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(graph)))
    handler = BackgroundHandler(settings, store, None, wa)
    await handler(Queue.BACKGROUND, {"kind": "export", "user_id": uid, "lang": "en"})
    upload, send = calls
    assert b'"Priya"' in upload[1] and b"meri shaadi?" in upload[1] and WA.encode() in upload[1]
    assert send[1]["type"] == "document" and send[1]["to"] == WA
    assert [a.action for a in store.audit_log] == ["export"]
