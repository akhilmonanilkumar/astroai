"""Sender: delivers outbound WhatsApp messages with human-like pacing.

- `typing` jobs: blue ticks + typing dots, best effort, never retried.
- `bubbles` jobs: one reply's bubbles, sent in order under a per-user send lock so two
  replies never interleave. Each bubble is marked sent, so a retry resumes where it stopped.
  Reply buttons, if any, go on the last bubble. A reply marked `voice` is spoken
  (Sarvam TTS → OGG/Opus → WhatsApp voice note); if speech fails it goes out as text.
"""

import asyncio
import logging
from typing import Any

from redis.asyncio import Redis

from guruji.config import Settings
from guruji.logs import user_tag
from guruji.queue.locks import send_lock_key, try_lock
from guruji.queue.streams import PermanentJobError, Queue, RetryJob
from guruji.voice.speech import Speech, SpeechError
from guruji.whatsapp.client import WhatsAppClient, WhatsAppError

log = logging.getLogger(__name__)


def sent_key(turn_id: str, index: int | str) -> str:
    return f"sent:{turn_id}:{index}"


def pace_seconds(text: str, settings: Settings) -> float:
    raw = len(text) / settings.bubble_chars_per_second
    return max(settings.bubble_min_delay, min(settings.bubble_max_delay, raw))


class SendHandler:
    def __init__(
        self,
        redis: Redis,
        settings: Settings,
        client: WhatsAppClient,
        speech: Speech | None = None,
    ) -> None:
        self.redis = redis
        self.settings = settings
        self.client = client
        self.speech = speech

    async def __call__(self, queue: Queue, job: dict[str, Any]) -> None:
        kind = job.get("kind")
        if kind == "typing":
            await self._typing(job)
        elif kind == "bubbles":
            await self._bubbles(job)
        else:
            raise PermanentJobError(f"unknown send job kind {kind!r}")

    async def _typing(self, job: dict[str, Any]) -> None:
        try:
            await self.client.mark_read_with_typing(job["message_id"])
        except WhatsAppError as e:
            log.warning("typing indicator failed user=%s status=%s", user_tag(job["to"]), e.status)

    async def _bubbles(self, job: dict[str, Any]) -> None:
        to, turn_id, bubbles = job["to"], job["turn_id"], job["bubbles"]
        buttons = [(b[0], b[1]) for b in job.get("buttons") or []]
        lock = send_lock_key(to)
        async with try_lock(self.redis, lock, self.settings.send_lock_seconds) as acquired:
            if not acquired:
                raise RetryJob(delay=0.5)
            voice = job.get("voice")
            if voice and self.speech is not None and await self._voice(to, turn_id, bubbles, voice):
                return
            for i, text in enumerate(bubbles):
                key = sent_key(turn_id, i)
                if await self.redis.exists(key):
                    continue
                if i > 0:
                    await asyncio.sleep(pace_seconds(text, self.settings))
                try:
                    if buttons and i == len(bubbles) - 1:
                        wamid = await self.client.send_buttons(to, text, buttons)
                    else:
                        wamid = await self.client.send_text(to, text)
                except WhatsAppError as e:
                    if e.retryable:
                        raise
                    raise PermanentJobError(str(e)) from e
                await self.redis.set(key, wamid, ex=self.settings.dedupe_ttl_seconds)
        log.info("sent user=%s turn=%s bubbles=%d", user_tag(to), turn_id, len(bubbles))

    async def _voice(self, to: str, turn_id: str, bubbles: list[str], language: str) -> bool:
        """Send the whole reply as one voice note. False: fall back to text bubbles."""
        key = sent_key(turn_id, "voice")
        if await self.redis.exists(key):
            return True
        assert self.speech is not None
        try:
            audio = await self.speech.synthesize("\n\n".join(bubbles), language)
            media_id = await self.client.upload_media(audio, "audio/ogg", "guruji.ogg")
            wamid = await self.client.send_audio(to, media_id)
        except SpeechError as e:
            log.warning("voice reply fell back to text user=%s: %s", user_tag(to), e)
            return False
        except WhatsAppError as e:
            if e.retryable:
                raise
            log.warning("voice reply fell back to text user=%s: %s", user_tag(to), e.status)
            return False
        await self.redis.set(key, wamid, ex=self.settings.dedupe_ttl_seconds)
        log.info("sent voice user=%s turn=%s bytes=%d", user_tag(to), turn_id, len(audio))
        return True
