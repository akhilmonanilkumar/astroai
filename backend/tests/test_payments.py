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
from guruji.billing.razorpay import PaymentState, Razorpay, RazorpayError, valid_webhook
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


def _rzp_links(links: list[dict[str, Any]], create_status: int = 200) -> Razorpay:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/orders":
            return httpx.Response(200, json={"items": []})  # no WhatsApp order: a link
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["reference_id"] == "gj1" and body["notify"] == {
                "sms": False,
                "email": False,
            }
            assert "customer" not in body  # Razorpay needn't know who the user is
            if create_status != 200:
                return httpx.Response(create_status, json={"error": {"code": "BAD_REQUEST"}})
            return httpx.Response(200, json={"short_url": "https://rzp.io/new"})
        return httpx.Response(200, json={"payment_links": links})

    return Razorpay(
        "k", "s", "https://rzp.test", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def _link(status: str, *payments: dict[str, Any], ref: str = "gj1") -> dict[str, Any]:
    return {"reference_id": ref, "status": status, "short_url": "https://rzp.io/old",
            "payments": list(payments) or None}  # fmt: skip


async def test_razorpay_payment_link_check() -> None:
    captured = {"payment_id": "pay_9", "status": "captured", "amount": 5100}
    paid = await _rzp_links([_link("paid", captured)]).check("gj1", 5100)
    assert paid == PaymentState("paid", "pay_9", 5100)
    failed_try = {"payment_id": "pay_8", "status": "failed", "amount": 5100}
    # a failed attempt can be retried on the same link: still pending
    assert (await _rzp_links([_link("created", failed_try)]).check("gj1", 5100)).status == (
        "pending"
    )
    assert (await _rzp_links([_link("expired")]).check("gj1", 5100)).status == "failed"
    # the reference_id filter isn't trusted: someone else's paid link is not ours
    other = _link("paid", captured, ref="gj2")
    assert (await _rzp_links([other]).check("gj1", 5100)).status == "pending"


async def test_razorpay_create_link_is_idempotent() -> None:
    assert await _rzp_links([]).create_link("gj1", 5100, "10 questions", 2_000_000_000) == (
        "https://rzp.io/new"
    )
    # Razorpay refuses a reused reference_id: the existing link is returned instead
    again = _rzp_links([_link("created")], create_status=400)
    assert await again.create_link("gj1", 5100, "10 questions", 2_000_000_000) == (
        "https://rzp.io/old"
    )
    with pytest.raises(RazorpayError):
        await _rzp_links([], create_status=400).create_link("gj1", 5100, "x", 2_000_000_000)


@pytest.mark.parametrize("lang", ["en", "hinglish", "hi"])
def test_link_checkout_card(lang: Any) -> None:
    item = catalog.items(PACKS, PASSES)["p101"]
    card = catalog.link_checkout(item, "https://rzp.io/abc", lang)
    params = card["action"]["parameters"]
    assert card["type"] == "cta_url" and params["url"] == "https://rzp.io/abc"
    assert "101" in params["display_text"] and len(params["display_text"]) <= 20


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
        # payment_link.paid also carries Razorpay's own order: its receipt isn't ours
        link_body = json.dumps(
            {
                "event": "payment_link.paid",
                "payload": {
                    "payment_link": {"entity": {"reference_id": "gj6"}},
                    "order": {"entity": {"receipt": "rzp-internal"}},
                },
            }
        ).encode()
        sig = hmac.new(secret.encode(), link_body, hashlib.sha256).hexdigest()
        r = await c.post(
            "/razorpay/webhook", content=link_body, headers={"X-Razorpay-Signature": sig}
        )
        assert r.status_code == 200
    refs = [j["reference_id"] for j in await _jobs(redis, "payment")]
    assert refs == ["gj9", "gj8", "gj6"]


async def test_turn_worker_starts_checking_a_link_payment(redis: Any, settings: Settings) -> None:
    from guruji.turn.reply import Reply
    from guruji.turn.worker import TurnHandler
    from guruji.whatsapp.models import IncomingMessage

    class Checkout:
        async def respond(self, turn: Any) -> Reply:
            return Reply(["pay here"], kind="checkout", payment_check="gj5")

    msg = IncomingMessage(wamid="w1", wa_id=WA, kind="text", text="buy", ts=0)
    await TurnHandler(redis, settings, Checkout())(
        Queue.TURN, {"wa_id": WA, "messages": [msg.model_dump()]}
    )
    [job] = await _jobs(redis, "payment")
    assert (job["kind"], job["reference_id"], job["_id"]) == ("check", "gj5", "paycheck:sent:gj5")


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


async def test_simulator_plays_razorpay_payment_links(settings: Settings) -> None:
    from guruji.simulator.app import create_app as create_sim

    sim = create_sim(settings)
    auth = (settings.razorpay_key_id, settings.razorpay_key_secret.get_secret_value())
    rzp = Razorpay(
        *auth,
        "http://s/razorpay",
        httpx.AsyncClient(transport=httpx.ASGITransport(app=sim)),
    )
    url = await rzp.create_link("gj4", 5100, "10 questions", 2_000_000_000)
    assert await rzp.create_link("gj4", 5100, "10 questions", 2_000_000_000) == url
    assert (await rzp.check("gj4", 5100)).status == "pending"
    card = catalog.link_checkout(catalog.items(PACKS, PASSES)["p51"], url, "en")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://s") as c:
        r = await c.post(
            f"/{settings.graph_api_version}/{settings.wa_phone_number_id}/messages",
            json={"to": WA, "type": "interactive", "interactive": card},
            headers={"Authorization": f"Bearer {settings.wa_access_token}"},
        )
        assert r.status_code == 200
        [event] = (await c.get("/api/history", params={"wa_id": WA})).json()
    assert [b["id"] for b in event["buttons"]] == ["__pay__:gj4", "__fail__:gj4"]


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
