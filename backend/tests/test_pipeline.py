"""End to end: webhook → coalescer → turn worker → sender → (mock) Graph API."""

import json
import time
from typing import Any

import httpx
import pytest

from conftest import signed, webhook
from guruji.config import Settings
from guruji.ingress.app import create_app
from guruji.ingress.coalescer import flush_due
from guruji.queue.streams import DEAD_STREAM, Queue, Worker
from guruji.sender.worker import SendHandler
from guruji.turn.worker import EchoResponder, TurnHandler, split_bubbles
from guruji.whatsapp.client import WhatsAppClient


class FakeGraph:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.fail_next = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.fail_next:
            self.fail_next -= 1
            return httpx.Response(500, json={"error": {"message": "down"}})
        body = json.loads(request.content)
        self.requests.append(body)
        if body.get("status") == "read":
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"messages": [{"id": f"wamid.OUT{len(self.requests)}"}]})

    @property
    def texts(self) -> list[str]:
        return [r["text"]["body"] for r in self.requests if r.get("type") == "text"]

    @property
    def typing(self) -> int:
        return sum(1 for r in self.requests if r.get("typing_indicator"))


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def pipeline(settings: Settings, redis: Any, graph: FakeGraph) -> Any:
    ingress = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings, redis)), base_url="http://ingress"
    )
    wa = WhatsAppClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(graph)))
    turn = Worker(
        redis,
        [Queue.CRISIS, Queue.TURN],
        TurnHandler(redis, settings, EchoResponder()),
        consumer="turn",
    )
    sender = Worker(redis, [Queue.SEND], SendHandler(redis, settings, wa), consumer="send")

    class P:
        async def post(self, payload: dict[str, Any]) -> None:
            raw, headers = signed(payload, settings.wa_app_secret)
            assert (await ingress.post("/webhook", content=raw, headers=headers)).status_code == 200

        async def drain(self) -> None:
            await flush_due(redis, now=time.time() + 60)
            await turn.run_once()
            await sender.run_once()

    return P()


async def test_two_rapid_messages_get_one_reply(pipeline: Any, graph: FakeGraph) -> None:
    await pipeline.post(webhook(wamid="wamid.1", text="hi"))
    await pipeline.post(webhook(wamid="wamid.2", text="meri shaadi kab hogi?"))
    await pipeline.drain()

    assert graph.typing == 1
    assert len(graph.texts) == 1
    assert "hi" in graph.texts[0] and "meri shaadi kab hogi?" in graph.texts[0]


async def test_replayed_webhook_gets_no_second_reply(pipeline: Any, graph: FakeGraph) -> None:
    payload = webhook(wamid="wamid.1", text="hi")
    await pipeline.post(payload)
    await pipeline.drain()
    await pipeline.post(payload)  # Meta retry
    await pipeline.drain()
    assert len(graph.texts) == 1


async def test_graph_outage_is_retried(
    pipeline: Any, graph: FakeGraph, redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_sleep(_: float) -> None:
        return None

    real = time.time
    monkeypatch.setattr("guruji.queue.streams.asyncio.sleep", no_sleep)
    monkeypatch.setattr("guruji.queue.streams.time.time", lambda: real() + 3600)

    await pipeline.post(webhook(wamid="wamid.1", text="hi"))
    graph.fail_next = 3  # typing (best effort, swallowed) + 2 failed text sends
    await pipeline.drain()
    assert len(graph.texts) == 1
    assert await redis.xlen(DEAD_STREAM) == 0


def test_split_bubbles() -> None:
    assert split_bubbles("a\n\nb\n\nc", 2) == ["a", "b\n\nc"]
    assert split_bubbles("one", 2) == ["one"]
    assert split_bubbles("  \n\n ", 2) == []
