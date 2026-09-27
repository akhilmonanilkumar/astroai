"""Voice notes: Sarvam speech-to-text (in) and text-to-speech (out).

WhatsApp voice notes are OGG/Opus. Sarvam transcribes that directly, and its TTS returns
OGG/Opus when asked for the "opus" codec, so no ffmpeg or re-encoding is needed.
"""

import asyncio
import base64
import logging
import re
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import SecretStr

from guruji.agent.language import Language
from guruji.voice import ogg

log = logging.getLogger(__name__)

STT_MODEL = "saaras:v3"
TTS_MODEL = "bulbul:v3"
MAX_TTS_CHARS = 2400  # bulbul:v3 accepts 2500
# A WhatsApp voice reply should be ~30 s; spoken Hindi runs ~13 characters a second.
MAX_SPOKEN_CHARS = 450
STT_MAX_SECONDS = 28.0  # Sarvam's real-time STT takes at most 30 s per request


class SpeechError(Exception):
    pass


@dataclass(frozen=True)
class Transcript:
    text: str
    language_code: str | None  # BCP-47, e.g. "hi-IN"


class Speech(Protocol):
    async def transcribe(self, audio: bytes, mime: str) -> Transcript: ...
    async def synthesize(self, text: str, language_code: str) -> bytes:
        """OGG/Opus audio, ready to upload as a WhatsApp voice note."""
        ...


def tts_language(lang: Language) -> str:
    return "en-IN" if lang == "en" else "hi-IN"


_EMOJI = re.compile("[🌀-🫿☀-➿🀀-🋿‍️]+")
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")


def speakable(text: str, limit: int = MAX_TTS_CHARS) -> str:
    """Text as it should be spoken: no emoji or markdown, cut at a sentence boundary."""
    text = _EMOJI.sub("", text)
    text = re.sub(r"[*_#`>]+", "", text)
    text = re.sub(r"\s*\n+\s*", " ", text).strip()
    if len(text) <= limit:
        return text
    out = ""
    for sentence in _SENTENCE_END.split(text):
        if len(out) + len(sentence) + 1 > limit:
            break
        out = f"{out} {sentence}".strip()
    return out or text[:limit]


class SarvamSpeech:
    def __init__(
        self,
        api_key: SecretStr,
        speaker: str,
        base_url: str = "https://api.sarvam.ai",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._headers = {"api-subscription-key": api_key.get_secret_value()}
        self._base = base_url.rstrip("/")
        self._speaker = speaker
        self._http = http or httpx.AsyncClient(timeout=60.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def transcribe(self, audio: bytes, mime: str) -> Transcript:
        """Long OGG/Opus notes are split into <30 s chunks, transcribed in parallel, joined."""
        chunks = [audio]
        if ogg.is_ogg_opus(audio):
            try:
                chunks = ogg.split(audio, STT_MAX_SECONDS)
            except ogg.OggError as e:
                raise SpeechError("unreadable voice note") from e
        parts = await asyncio.gather(*(self._transcribe_one(c, mime) for c in chunks))
        text = " ".join(p.text for p in parts if p.text)
        return Transcript(text, next((p.language_code for p in parts if p.language_code), None))

    async def _transcribe_one(self, audio: bytes, mime: str) -> Transcript:
        ext = "ogg" if "ogg" in mime or "opus" in mime else mime.rsplit("/", 1)[-1]
        try:
            r = await self._http.post(
                f"{self._base}/speech-to-text",
                headers=self._headers,
                files={"file": (f"voice.{ext}", audio, mime)},
                # codemix keeps Hinglish as spoken: Hindi in Devanagari, English words as is
                data={"model": STT_MODEL, "language_code": "unknown", "mode": "codemix"},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise SpeechError(f"speech-to-text failed: {type(e).__name__}") from e
        data = r.json()
        return Transcript(str(data.get("transcript") or "").strip(), data.get("language_code"))

    async def synthesize(self, text: str, language_code: str) -> bytes:
        try:
            r = await self._http.post(
                f"{self._base}/text-to-speech",
                headers=self._headers,
                json={
                    "text": speakable(text, MAX_SPOKEN_CHARS),
                    "language_code": language_code,
                    "model": TTS_MODEL,
                    "speaker": self._speaker,
                    "output_audio_codec": "opus",
                    "speech_sample_rate": 24000,
                },
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise SpeechError(f"text-to-speech failed: {type(e).__name__}") from e
        audio = base64.b64decode(r.json()["audios"][0])
        if audio[:4] != b"OggS":
            raise SpeechError("text-to-speech did not return OGG audio")
        return audio
