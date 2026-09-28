"""Webhook ingress: verify, dedupe, buffer, ack. No business logic, no DB, target < 50 ms."""

import hmac
import json
import logging

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError
from redis.asyncio import Redis

from guruji.billing.razorpay import valid_webhook
from guruji.config import Settings
from guruji.ingress.coalescer import ingest
from guruji.logs import user_tag
from guruji.queue.streams import Queue, enqueue
from guruji.whatsapp import signature
from guruji.whatsapp.models import WebhookPayload, extract_messages, extract_payment_refs

log = logging.getLogger(__name__)


def seen_key(wamid: str) -> str:
    return f"seen:wamid:{wamid}"


def create_app(settings: Settings, redis: Redis) -> FastAPI:
    app = FastAPI(title="guruji-ingress", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        await redis.ping()
        return {"ok": True}

    @app.get("/webhook", response_class=PlainTextResponse)
    async def verify_subscription(
        mode: str = Query("", alias="hub.mode"),
        token: str = Query("", alias="hub.verify_token"),
        challenge: str = Query("", alias="hub.challenge"),
    ) -> str:
        if mode == "subscribe" and token == settings.wa_verify_token:
            return challenge
        raise HTTPException(status_code=403)

    @app.post("/webhook")
    async def receive(request: Request) -> dict[str, bool]:
        body = await request.body()
        if not signature.verify(
            body, request.headers.get(signature.SIGNATURE_HEADER), settings.wa_app_secret
        ):
            raise HTTPException(status_code=401)
        try:
            payload = WebhookPayload.model_validate_json(body)
        except ValidationError:
            log.warning("unparseable webhook payload ignored")
            return {"ok": True}  # 200 so Meta does not keep retrying garbage

        for msg in extract_messages(payload):
            fresh = await redis.set(
                seen_key(msg.wamid), "1", nx=True, ex=settings.dedupe_ttl_seconds
            )
            if not fresh:
                log.info("duplicate delivery ignored user=%s", user_tag(msg.wa_id))
                continue
            try:
                await ingest(redis, settings, msg)
            except Exception:
                # Not buffered: forget we saw it so Meta's retry (after our 500) gets in.
                await redis.delete(seen_key(msg.wamid))
                raise
        for ref in extract_payment_refs(payload):
            await _check_payment(ref)
        return {"ok": True}

    async def _check_payment(ref: str) -> None:
        """Webhooks never credit anything: they only ask the payment worker to look."""
        await enqueue(
            redis,
            Queue.PAYMENT,
            {"kind": "check", "reference_id": ref},
            job_id=f"paycheck:{ref}",
            dedupe_ttl=60,  # a later webhook for the same order may still be worth a look
        )

    @app.post("/razorpay/webhook")
    async def razorpay(request: Request) -> dict[str, bool]:
        body = await request.body()
        sent = request.headers.get("X-Razorpay-Signature", "")
        if not valid_webhook(body, sent, settings.razorpay_webhook_secret.get_secret_value()):
            raise HTTPException(status_code=401)
        try:
            event = json.loads(body)
            payload = event.get("payload") or {}
            order = (payload.get("order") or {}).get("entity") or {}
            payment = (payload.get("payment") or {}).get("entity") or {}
            link = (payload.get("payment_link") or {}).get("entity") or {}
            # A link's own reference first: payment_link events carry an order too, and
            # that order's receipt is Razorpay's, not ours.
            ref = (
                link.get("reference_id")
                or order.get("receipt")
                or (payment.get("notes") or {}).get("reference_id")
            )
        except (ValueError, AttributeError):
            return {"ok": True}
        if isinstance(ref, str) and 0 < len(ref) <= 64:
            await _check_payment(ref)
        return {"ok": True}

    @app.post("/telegram/webhook")
    async def telegram(request: Request) -> dict[str, bool]:
        """Button taps on team alerts. Only queued here; the alerts worker does the rest."""
        secret = settings.telegram_webhook_secret
        sent = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret is None or not hmac.compare_digest(sent, secret.get_secret_value()):
            raise HTTPException(status_code=401)
        update = await request.json()
        cb = update.get("callback_query") or {}
        data = str(cb.get("data", ""))
        if data.startswith("ack:"):
            esc_id = data[4:]
            await enqueue(
                redis,
                Queue.ALERT,
                {
                    "kind": "ack",
                    "escalation_id": esc_id,
                    "by": f"telegram:{(cb.get('from') or {}).get('id', '')}",
                    "callback_id": cb.get("id"),
                },
                job_id=f"ack:{esc_id}:{cb.get('id')}",
            )
        return {"ok": True}

    return app
