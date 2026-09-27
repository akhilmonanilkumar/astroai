"""M5 plumbing: crisis priority, Telegram webhook, alerts, voice sending, media, speech."""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from guruji.alerts.worker import AlertHandler, LogAlerter, alert_text, reping_due
from guruji.config import Settings
from guruji.db.models import EscalationOpen, InboundLog, TurnWrite
from guruji.db.store import MemoryStore
from guruji.ingress.app import create_app
from guruji.ingress.coalescer import flush_due, ingest
from guruji.queue.streams import Queue
from guruji.sender.worker import SendHandler
from guruji.voice.speech import SarvamSpeech, SpeechError, Transcript, speakable
from guruji.whatsapp.client import WhatsAppClient
from guruji.whatsapp.models import IncomingMessage

WA = "919800000001"


async def _jobs(redis: Any, queue: str) -> list[dict[str, Any]]:
    return [json.loads(f["job"]) for _, f in await redis.xrange(f"q:{queue}")]


# --- crisis priority --------------------------------------------------------------------


async def test_urgent_burst_skips_the_wait_and_jumps_the_queue(
    redis: Any, settings: Settings
) -> None:
    await ingest(
        redis,
        settings,
        IncomingMessage(wa_id=WA, wamid="w1", ts=0, kind="text", text="hi"),
        now=100.0,
    )
    await ingest(
        redis,
        settings,
        IncomingMessage(wa_id=WA, wamid="w2", ts=0, kind="text", text="I want to kill myself"),
        now=100.5,
    )
    assert await flush_due(redis, now=100.5) == 1  # no quiet window for an emergency
    [job] = await _jobs(redis, "crisis")
    assert [m["text"] for m in job["messages"]] == ["hi", "I want to kill myself"]
    assert await _jobs(redis, "turn") == []


async def test_ordinary_burst_still_goes_to_turn(redis: Any, settings: Settings) -> None:
    await ingest(
        redis,
        settings,
        IncomingMessage(wa_id=WA, wamid="w1", ts=0, kind="text", text="shaadi kab hogi"),
        now=100.0,
    )
    assert await flush_due(redis, now=100.5) == 0
    assert await flush_due(redis, now=103.0) == 1
    assert len(await _jobs(redis, "turn")) == 1


# --- Telegram webhook ------------------------------------------------------------------


async def test_telegram_ack_is_queued_only_with_the_secret(settings: Settings, redis: Any) -> None:
    settings.telegram_webhook_secret = SecretStr("s3cret")
    app = create_app(settings, redis)
    update = {
        "update_id": 1,
        "callback_query": {"id": "cb1", "from": {"id": 42}, "data": "ack:esc-1"},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://ingress"
    ) as c:
        bad = await c.post("/telegram/webhook", json=update)
        good = await c.post(
            "/telegram/webhook",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
        )
    assert (bad.status_code, good.status_code) == (401, 200)
    [job] = await _jobs(redis, "alert")
    job.pop("_id", None)
    assert job == {
        "kind": "ack",
        "escalation_id": "esc-1",
        "by": "telegram:42",
        "callback_id": "cb1",
    }


# --- alerts -----------------------------------------------------------------------------


async def _store_with_escalation(severity: int = 1) -> tuple[MemoryStore, str]:
    store = MemoryStore()
    user, _ = await store.get_or_create_user("h")
    await store.commit_turn(
        TurnWrite(
            user.id,
            "t1",
            [InboundLog("w", "text", None)],
            reply_body="x",
            state="escalated",
            escalation=EscalationOpen("esc-1", "crisis", severity, "active"),
        )
    )
    return store, user.id


async def test_alert_is_sent_without_personal_data_and_acknowledged() -> None:
    store, user_id = await _store_with_escalation()
    alerter = LogAlerter("https://admin.test")
    handler = AlertHandler(store, alerter)
    await handler(Queue.ALERT, {"kind": "escalation", "escalation_id": "esc-1"})
    [text] = alerter.sent
    assert "crisis" in text and "esc-1" in text and "https://admin.test/escalations/esc-1" in text
    assert user_id not in text and WA not in text
    esc = await store.get_escalation("esc-1")
    assert esc is not None and esc.alert_count == 1 and esc.last_alerted_at is not None
    await handler(Queue.ALERT, {"kind": "ack", "escalation_id": "esc-1", "by": "telegram:1"})
    esc = await store.get_escalation("esc-1")
    assert esc is not None and esc.status == "acknowledged"


async def test_unacknowledged_crisis_is_repinged(redis: Any, settings: Settings) -> None:
    store, _ = await _store_with_escalation()
    await store.mark_alerted("esc-1")
    store.escalations["esc-1"].last_alerted_at = datetime.now(UTC) - timedelta(minutes=6)
    assert await reping_due(redis, store, settings) == 1
    assert await reping_due(redis, store, settings) == 1  # same job id: deduped on the queue
    assert len(await _jobs(redis, "alert")) == 1
    await store.acknowledge_escalation("esc-1", "telegram:1")
    assert await reping_due(redis, store, settings) == 0


async def test_resolved_escalation_is_not_alerted() -> None:
    store, _ = await _store_with_escalation()
    await store.resolve_escalation("esc-1", hand_back=False)
    alerter = LogAlerter("https://admin.test")
    await AlertHandler(store, alerter)(
        Queue.ALERT, {"kind": "escalation", "escalation_id": "esc-1"}
    )
    assert alerter.sent == []


async def test_alert_text_marks_reminders() -> None:
    store, _ = await _store_with_escalation()
    await store.mark_alerted("esc-1")
    esc = await store.get_escalation("esc-1")
    assert esc is not None and "(reminder 1)" in alert_text(esc, "https://a.test")


# --- voice sending -----------------------------------------------------------------------


class Graph:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/media") and request.method == "POST":
            self.calls.append(("upload", request.headers["content-type"]))
            return httpx.Response(200, json={"id": "media-9"})
        if request.method == "GET" and path.endswith("/dl/in-1"):
            assert request.headers["authorization"].startswith("Bearer ")
            return httpx.Response(200, content=b"OggS-in")
        if request.method == "GET" and path.endswith("/in-1"):
            return httpx.Response(
                200,
                json={"url": "http://graph.test/dl/in-1", "mime_type": "audio/ogg; codecs=opus"},
            )
        body = json.loads(request.content)
        self.calls.append((body.get("type", "?"), body))
        return httpx.Response(200, json={"messages": [{"id": f"wamid.O{len(self.calls)}"}]})


class Speech:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def transcribe(self, audio: bytes, mime: str) -> Transcript:
        return Transcript("x", None)

    async def synthesize(self, text: str, language_code: str) -> bytes:
        if self.fail:
            raise SpeechError("tts down")
        return b"OggS" + text.encode()


def _job(**kw: Any) -> dict[str, Any]:
    return {"kind": "bubbles", "to": WA, "turn_id": "t9", "bubbles": ["one", "two"], **kw}


async def test_voice_reply_is_one_voice_note(settings: Settings, redis: Any) -> None:
    graph = Graph()
    client = WhatsAppClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(graph)))
    handler = SendHandler(redis, settings, client, Speech())
    await handler(Queue.SEND, _job(voice="hi-IN"))
    assert [c[0] for c in graph.calls] == ["upload", "audio"]
    assert graph.calls[1][1]["audio"] == {"id": "media-9", "voice": True}
    await handler(Queue.SEND, _job(voice="hi-IN"))  # redelivery: nothing sent twice
    assert len(graph.calls) == 2


async def test_voice_falls_back_to_text(settings: Settings, redis: Any) -> None:
    graph = Graph()
    client = WhatsAppClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(graph)))
    await SendHandler(redis, settings, client, Speech(fail=True))(Queue.SEND, _job(voice="hi-IN"))
    assert [c[1]["text"]["body"] for c in graph.calls] == ["one", "two"]


async def test_download_media(settings: Settings) -> None:
    client = WhatsAppClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(Graph())))
    assert await client.download_media("in-1") == (b"OggS-in", "audio/ogg; codecs=opus")


# --- Sarvam speech client ---------------------------------------------------------------


def _sarvam(handler: Any) -> SarvamSpeech:
    return SarvamSpeech(
        SecretStr("k"),
        "aditya",
        "http://sarvam.test",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_sarvam_transcribe_and_synthesize() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request
        assert request.headers["api-subscription-key"] == "k"
        if request.url.path == "/speech-to-text":
            return httpx.Response(200, json={"transcript": " namaste ", "language_code": "hi-IN"})
        body = json.loads(request.content)
        assert body["output_audio_codec"] == "opus" and body["speaker"] == "aditya"
        assert "🙏" not in body["text"]
        return httpx.Response(200, json={"audios": [base64.b64encode(b"OggS..").decode()]})

    sp = _sarvam(handler)
    assert await sp.transcribe(b"OggS", "audio/ogg") == Transcript("namaste", "hi-IN")
    assert await sp.synthesize("Namaste ji 🙏", "hi-IN") == b"OggS.."


async def test_sarvam_errors_become_speech_errors() -> None:
    sp = _sarvam(lambda r: httpx.Response(503, json={}))
    with pytest.raises(SpeechError):
        await sp.transcribe(b"x", "audio/ogg")
    wav = _sarvam(
        lambda r: httpx.Response(200, json={"audios": [base64.b64encode(b"RIFF").decode()]})
    )
    with pytest.raises(SpeechError):
        await wav.synthesize("hi", "en-IN")  # not OGG: WhatsApp would not play it as voice


def test_speakable() -> None:
    assert speakable("**Namaste** ji 🙏\n\nKaise hain?") == "Namaste ji Kaise hain?"
    long = "Ek vaakya hai. " * 400
    out = speakable(long, limit=100)
    assert len(out) <= 100 and out.endswith(".")
