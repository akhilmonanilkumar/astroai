from typing import Any

import httpx
import pytest

from conftest import signed, webhook
from guruji.config import Settings
from guruji.ingress.app import create_app
from guruji.ingress.coalescer import DUE_KEY, msgs_key


@pytest.fixture
async def client(settings: Settings, redis: Any) -> Any:
    app = create_app(settings, redis)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ingress") as c:
        yield c


async def test_subscription_verify(client: httpx.AsyncClient, settings: Settings) -> None:
    ok = await client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": settings.wa_verify_token,
            "hub.challenge": "1158201444",
        },
    )
    assert ok.status_code == 200 and ok.text == "1158201444"
    bad = await client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "nope",
            "hub.challenge": "x",
        },
    )
    assert bad.status_code == 403


async def test_rejects_bad_signature(client: httpx.AsyncClient, redis: Any) -> None:
    raw, headers = signed(webhook(), "wrong-secret")
    r = await client.post("/webhook", content=raw, headers=headers)
    assert r.status_code == 401
    assert await redis.zcard(DUE_KEY) == 0


async def test_buffers_message_and_queues_typing(
    client: httpx.AsyncClient, redis: Any, settings: Settings
) -> None:
    raw, headers = signed(webhook(), settings.wa_app_secret)
    r = await client.post("/webhook", content=raw, headers=headers)
    assert r.status_code == 200
    assert await redis.llen(msgs_key("919800000001")) == 1
    assert await redis.xlen("q:send") == 1  # typing indicator job


async def test_replayed_webhook_is_deduped(
    client: httpx.AsyncClient, redis: Any, settings: Settings
) -> None:
    raw, headers = signed(webhook(), settings.wa_app_secret)
    for _ in range(3):
        assert (await client.post("/webhook", content=raw, headers=headers)).status_code == 200
    assert await redis.llen(msgs_key("919800000001")) == 1
    assert await redis.xlen("q:send") == 1


async def test_garbage_payload_is_acked(client: httpx.AsyncClient, settings: Settings) -> None:
    raw, headers = signed(
        {"object": "whatsapp_business_account", "entry": "nope"}, settings.wa_app_secret
    )
    assert (await client.post("/webhook", content=raw, headers=headers)).status_code == 200


async def test_failed_buffering_lets_the_retry_in(
    redis: Any, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a message can't be buffered, Meta's retry must not be dropped as a duplicate."""
    from guruji.ingress import app as ingress_app

    real = ingress_app.ingest
    calls = [0]

    async def flaky(*args: Any, **kwargs: Any) -> None:
        calls[0] += 1
        if calls[0] == 1:
            raise ConnectionError("redis blip")
        await real(*args, **kwargs)

    monkeypatch.setattr(ingress_app, "ingest", flaky)
    transport = httpx.ASGITransport(app=create_app(settings, redis), raise_app_exceptions=False)
    raw, headers = signed(webhook(), settings.wa_app_secret)
    async with httpx.AsyncClient(transport=transport, base_url="http://ingress") as c:
        first = await c.post("/webhook", content=raw, headers=headers)
        assert first.status_code == 500  # Meta retries non-2xx deliveries
        retry = await c.post("/webhook", content=raw, headers=headers)
    assert retry.status_code == 200
    assert await redis.llen(msgs_key("919800000001")) == 1
