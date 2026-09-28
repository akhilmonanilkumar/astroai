"""Turn worker: one merged burst in, one reply (1-2 bubbles, maybe buttons) out.

The `Responder` is the guru graph (guruji.agent.graph); `EchoResponder` remains for
pipeline tests.
"""

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from redis.asyncio import Redis

from guruji.config import Settings
from guruji.logs import user_tag
from guruji.queue.locks import try_lock, turn_lock_key
from guruji.queue.streams import Queue, RetryJob, enqueue
from guruji.turn.reply import Reply
from guruji.whatsapp.models import IncomingMessage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Turn:
    turn_id: str
    wa_id: str
    messages: list[IncomingMessage]

    @property
    def text(self) -> str:
        return "\n".join(m.text for m in self.messages if m.text)


class Responder(Protocol):
    async def respond(self, turn: Turn) -> Reply: ...


class EchoResponder:
    """Echoes the burst back: proves the pipeline end to end without an LLM."""

    async def respond(self, turn: Turn) -> Reply:
        parts = []
        for m in turn.messages:
            if m.kind == "audio":
                parts.append("[voice note]")
            elif m.kind == "unsupported":
                parts.append("[unsupported message]")
            else:
                parts.append(m.text)
        text = f"🙏 Echo ({len(turn.messages)} msg):\n" + "\n".join(parts)
        return Reply(split_bubbles(text, 2))


def turn_id_for(messages: list[IncomingMessage]) -> str:
    joined = "|".join(sorted(m.wamid for m in messages))
    return "t_" + hashlib.sha256(joined.encode()).hexdigest()[:24]


def split_bubbles(text: str, max_bubbles: int, max_chars: int = 4000) -> list[str]:
    """Split on paragraph breaks into at most `max_bubbles` WhatsApp messages."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paras:
        return []
    if len(paras) > max_bubbles:
        head = paras[: max_bubbles - 1]
        paras = [*head, "\n\n".join(paras[max_bubbles - 1 :])]
    return [p[:max_chars] for p in paras]


def done_key(turn_id: str) -> str:
    return f"done:turn:{turn_id}"


class TurnHandler:
    def __init__(self, redis: Redis, settings: Settings, responder: Responder) -> None:
        self.redis = redis
        self.settings = settings
        self.responder = responder

    async def __call__(self, queue: Queue, job: dict[str, Any]) -> None:
        messages = [IncomingMessage.model_validate(m) for m in job["messages"]]
        for i, m in enumerate(messages):
            if m.kind == "reaction" and m.reacted_to:
                target = await self.redis.get(f"out:{m.reacted_to}")
                messages[i] = m.model_copy(update={"reply_id": target})
        turn = Turn(turn_id=turn_id_for(messages), wa_id=job["wa_id"], messages=messages)

        if await self.redis.exists(done_key(turn.turn_id)):
            return  # redelivered after we already replied

        lock = turn_lock_key(turn.wa_id)
        async with try_lock(self.redis, lock, self.settings.turn_lock_seconds) as acquired:
            if not acquired:
                raise RetryJob(delay=1.0)
            reply = await self.responder.respond(turn)
            bubbles = reply.bubbles
            if bubbles:
                await enqueue(
                    self.redis,
                    Queue.SEND,
                    {
                        "kind": "bubbles",
                        "to": turn.wa_id,
                        "turn_id": turn.turn_id,
                        "bubbles": bubbles,
                        "buttons": [[b.id, b.title] for b in reply.buttons],
                        "voice": reply.voice_language,
                        "interactive": reply.interactive,
                    },
                    job_id=f"send:{turn.turn_id}",
                )
            if reply.alert_escalation_id:
                await enqueue(
                    self.redis,
                    Queue.ALERT,
                    {"kind": "escalation", "escalation_id": reply.alert_escalation_id},
                    job_id=f"alert:{reply.alert_escalation_id}:{turn.turn_id}",
                )
            if reply.kind == "first_reading":  # onboarded: a Lead for the ad that brought them
                await enqueue(
                    self.redis,
                    Queue.BACKGROUND,
                    {"kind": "capi", "event": "Lead", "wa_id": turn.wa_id},
                    job_id=f"capi:lead:{turn.wa_id}",
                    dedupe_ttl=30 * 24 * 3600,
                )
            await self.redis.set(done_key(turn.turn_id), "1", ex=self.settings.dedupe_ttl_seconds)
        log.info(
            "turn done user=%s turn=%s msgs=%d bubbles=%d",
            user_tag(turn.wa_id),
            turn.turn_id,
            len(messages),
            len(bubbles),
        )
