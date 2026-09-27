"""WhatsApp simulator.

Plays both sides Meta normally plays:
- User side: a chat page that turns what you type into signed, Meta-shaped webhooks
  and POSTs them to our ingress.
- Graph API side: `POST /{version}/{phone_number_id}/messages` receives what our sender
  sends (text, buttons, voice notes, typing) and streams it back to the page over SSE.
- Media: voice notes recorded on the page are stored here and served through the same
  two-step lookup as WhatsApp (`GET /{version}/{media_id}` → url → bytes); uploads from
  our sender (`POST /{version}/{phone_number_id}/media`) land here too.

Chaos controls let you make the fake Graph API fail to exercise retries.
"""

import asyncio
import json
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated, Any

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from guruji.config import Settings
from guruji.whatsapp import signature


class SendIn(BaseModel):
    wa_id: str
    name: str = "Test User"
    text: str | None = None
    reply_id: str | None = None
    reply_title: str | None = None


class ChaosIn(BaseModel):
    fail_next: int = 0
    status: int = 500


class SimState:
    def __init__(self) -> None:
        self.history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self.last_webhook: dict[str, bytes] = {}
        self.wamid_owner: dict[str, str] = {}
        self.fail_next = 0
        self.fail_status = 500
        self.media: dict[str, tuple[bytes, str]] = {}

    def add_media(self, data: bytes, mime: str) -> str:
        media_id = f"media{uuid.uuid4().hex[:16]}"
        self.media[media_id] = (data, mime)
        return media_id

    def publish(self, wa_id: str, event: dict[str, Any]) -> None:
        if event["type"] == "in":
            # remember the sender of each inbound wamid so read/typing events route back
            self.wamid_owner[event["wamid"]] = wa_id
        if event["type"] != "typing":
            self.history[wa_id].append(event)
        for q in self.subscribers[wa_id]:
            q.put_nowait(event)


def _webhook_payload(
    settings: Settings, body: SendIn, wamid: str, audio: dict[str, Any] | None = None
) -> dict[str, Any]:
    msg: dict[str, Any] = {"from": body.wa_id, "id": wamid, "timestamp": str(int(time.time()))}
    if audio is not None:
        msg["type"] = "audio"
        msg["audio"] = audio
    elif body.reply_id:
        msg["type"] = "interactive"
        msg["interactive"] = {
            "type": "button_reply",
            "button_reply": {"id": body.reply_id, "title": body.reply_title or body.reply_id},
        }
    else:
        msg["type"] = "text"
        msg["text"] = {"body": body.text or ""}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_SIM",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "910000000000",
                                "phone_number_id": settings.wa_phone_number_id,
                            },
                            "contacts": [{"wa_id": body.wa_id, "profile": {"name": body.name}}],
                            "messages": [msg],
                        },
                    }
                ],
            }
        ],
    }


def create_app(settings: Settings) -> FastAPI:
    http = httpx.AsyncClient(timeout=10.0)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await http.aclose()

    app = FastAPI(title="guruji-simulator", lifespan=lifespan)
    state = SimState()
    page = files("guruji.simulator").joinpath("page.html").read_text(encoding="utf-8")

    async def post_webhook(raw: bytes) -> int:
        resp = await http.post(
            settings.simulator_ingress_url,
            content=raw,
            headers={
                "Content-Type": "application/json",
                signature.SIGNATURE_HEADER: signature.sign(raw, settings.wa_app_secret),
            },
        )
        return resp.status_code

    # ---- user side ----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return page

    @app.post("/api/send")
    async def send(body: SendIn) -> dict[str, Any]:
        wamid = f"wamid.SIM{uuid.uuid4().hex}"
        raw = json.dumps(_webhook_payload(settings, body, wamid)).encode()
        state.last_webhook[body.wa_id] = raw
        state.publish(
            body.wa_id,
            {
                "type": "in",
                "text": body.reply_title or body.text,
                "wamid": wamid,
                "ts": time.time(),
            },
        )
        status = await post_webhook(raw)
        return {"wamid": wamid, "ingress_status": status}

    @app.post("/api/send_audio")
    async def send_audio(
        file: Annotated[UploadFile, File()],
        wa_id: Annotated[str, Form()],
        name: Annotated[str, Form()] = "Test User",
    ) -> dict[str, Any]:
        """A voice note recorded on the page (browser audio: webm/ogg opus)."""
        data = await file.read()
        mime = (file.content_type or "audio/ogg").split(";")[0]
        media_id = state.add_media(data, mime)
        wamid = f"wamid.SIM{uuid.uuid4().hex}"
        body = SendIn(wa_id=wa_id, name=name)
        audio = {"id": media_id, "mime_type": mime, "voice": True}
        raw = json.dumps(_webhook_payload(settings, body, wamid, audio)).encode()
        state.last_webhook[wa_id] = raw
        state.publish(
            wa_id,
            {"type": "in", "audio_url": f"/media/{media_id}", "wamid": wamid, "ts": time.time()},
        )
        return {"wamid": wamid, "ingress_status": await post_webhook(raw)}

    @app.get("/media/{media_id}")
    async def media_file(media_id: str) -> Response:
        if media_id not in state.media:
            raise HTTPException(404)
        data, mime = state.media[media_id]
        return Response(data, media_type=mime)

    @app.post("/api/replay")
    async def replay(wa_id: str) -> dict[str, Any]:
        """Re-deliver the last webhook byte-for-byte, like Meta does on retries."""
        raw = state.last_webhook.get(wa_id)
        if raw is None:
            raise HTTPException(404, "nothing to replay")
        return {"ingress_status": await post_webhook(raw)}

    @app.post("/api/chaos")
    async def chaos(body: ChaosIn) -> dict[str, int]:
        state.fail_next, state.fail_status = body.fail_next, body.status
        return {"fail_next": state.fail_next}

    @app.get("/api/history")
    async def history(wa_id: str) -> list[dict[str, Any]]:
        return state.history[wa_id]

    @app.get("/api/events")
    async def events(wa_id: str) -> StreamingResponse:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        state.subscribers[wa_id].add(q)

        async def stream() -> AsyncIterator[str]:
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(ev)}\n\n"
                    except TimeoutError:
                        yield ": ping\n\n"
            finally:
                state.subscribers[wa_id].discard(q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # ---- fake Graph API -------------------------------------------------------

    def _authorized(authorization: str) -> bool:
        return authorization == f"Bearer {settings.wa_access_token}"

    @app.post("/{version}/{phone_number_id}/media")
    async def graph_upload(
        version: str,
        phone_number_id: str,
        file: Annotated[UploadFile, File()],
        type: str = Form("audio/ogg"),
        authorization: str = Header(""),
    ) -> JSONResponse:
        if not _authorized(authorization):
            return JSONResponse({"error": {"message": "invalid token", "code": 190}}, 401)
        return JSONResponse({"id": state.add_media(await file.read(), type)})

    @app.get("/{version}/{media_id}")
    async def graph_media(
        version: str, media_id: str, authorization: str = Header("")
    ) -> JSONResponse:
        if not _authorized(authorization):
            return JSONResponse({"error": {"message": "invalid token", "code": 190}}, 401)
        if media_id not in state.media:
            return JSONResponse({"error": {"message": "unknown media"}}, 404)
        base = str(settings.graph_api_base).rstrip("/")
        return JSONResponse(
            {"url": f"{base}/media/{media_id}", "mime_type": state.media[media_id][1]}
        )

    @app.post("/{version}/{phone_number_id}/messages")
    async def graph_messages(
        version: str,
        phone_number_id: str,
        request: Request,
        authorization: str = Header(""),
    ) -> JSONResponse:
        if authorization != f"Bearer {settings.wa_access_token}":
            return JSONResponse({"error": {"message": "invalid token", "code": 190}}, 401)
        if phone_number_id != settings.wa_phone_number_id:
            return JSONResponse({"error": {"message": "unknown phone number id"}}, 400)
        if state.fail_next > 0:
            state.fail_next -= 1
            return JSONResponse({"error": {"message": "simulated failure"}}, state.fail_status)

        payload: dict[str, Any] = await request.json()
        if payload.get("status") == "read":
            owner = state.wamid_owner.get(payload.get("message_id", ""))
            if owner and payload.get("typing_indicator"):
                state.publish(owner, {"type": "typing", "ts": time.time()})
            return JSONResponse({"success": True})

        to = str(payload.get("to", ""))
        out_id = f"wamid.OUT{uuid.uuid4().hex}"
        event: dict[str, Any] = {"type": "out", "wamid": out_id, "ts": time.time()}
        if payload.get("type") == "text":
            event["text"] = payload["text"]["body"]
        elif payload.get("type") == "audio":
            event["audio_url"] = f"/media/{payload['audio']['id']}"
        elif payload.get("type") == "interactive":
            inter = payload["interactive"]
            event["text"] = inter.get("body", {}).get("text", "")
            event["buttons"] = [b["reply"] for b in inter.get("action", {}).get("buttons", [])]
        else:
            event["text"] = f"[{payload.get('type')} message]"
        state.publish(to, event)
        return JSONResponse(
            {
                "messaging_product": "whatsapp",
                "contacts": [{"input": to, "wa_id": to}],
                "messages": [{"id": out_id}],
            }
        )

    return app
