"""Minimal async client for the WhatsApp Cloud API `/messages` endpoint."""

from typing import Any

import httpx

from guruji.config import Settings


class WhatsAppError(Exception):
    def __init__(self, status: int, detail: str, *, retryable: bool) -> None:
        super().__init__(f"WhatsApp API {status}: {detail}")
        self.status = status
        self.retryable = retryable


class WhatsAppClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self._url = settings.graph_messages_url
        self._graph = f"{settings.graph_api_base.rstrip('/')}/{settings.graph_api_version}"
        self._media_upload_url = f"{self._graph}/{settings.wa_phone_number_id}/media"
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._headers = {"Authorization": f"Bearer {settings.wa_access_token}"}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send_text(self, to: str, body: str) -> str:
        return await self._send_message(
            to, {"type": "text", "text": {"preview_url": False, "body": body}}
        )

    async def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]]) -> str:
        """Up to 3 reply buttons; each is (id, title<=20 chars)."""
        if not 1 <= len(buttons) <= 3:
            raise ValueError("WhatsApp allows 1-3 reply buttons")
        action = {
            "buttons": [{"type": "reply", "reply": {"id": i, "title": t}} for i, t in buttons]
        }
        return await self._send_message(
            to,
            {
                "type": "interactive",
                "interactive": {"type": "button", "body": {"text": body}, "action": action},
            },
        )

    async def send_audio(self, to: str, media_id: str) -> str:
        """An uploaded OGG/Opus file, shown as a voice note (not an audio attachment)."""
        return await self._send_message(
            to, {"type": "audio", "audio": {"id": media_id, "voice": True}}
        )

    async def send_template(self, to: str, name: str, language: str) -> str:
        """An approved template (no parameters): the only way to write after 24 hours."""
        return await self._send_message(
            to, {"type": "template", "template": {"name": name, "language": {"code": language}}}
        )

    async def upload_media(self, data: bytes, mime: str, filename: str) -> str:
        try:
            resp = await self._http.post(
                self._media_upload_url,
                headers=self._headers,
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (filename, data, mime)},
            )
        except httpx.TransportError as e:
            raise WhatsAppError(0, type(e).__name__, retryable=True) from e
        self._raise_for(resp)
        return str(resp.json()["id"])

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """(bytes, mime type) of an inbound media message. Two steps: URL lookup, fetch."""
        try:
            meta = await self._http.get(f"{self._graph}/{media_id}", headers=self._headers)
            self._raise_for(meta)
            info = meta.json()
            data = await self._http.get(info["url"], headers=self._headers)
            self._raise_for(data)
        except httpx.TransportError as e:
            raise WhatsAppError(0, type(e).__name__, retryable=True) from e
        return data.content, str(info.get("mime_type") or "audio/ogg")

    async def mark_read_with_typing(self, message_id: str) -> None:
        """Blue ticks + typing indicator (shown up to ~25 s or until we reply)."""
        await self._post(
            {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
                "typing_indicator": {"type": "text"},
            }
        )

    async def _send_message(self, to: str, content: dict[str, Any]) -> str:
        data = await self._post(
            {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, **content}
        )
        try:
            return str(data["messages"][0]["id"])
        except (KeyError, IndexError, TypeError) as e:
            raise WhatsAppError(200, "response missing message id", retryable=False) from e

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._http.post(self._url, json=payload, headers=self._headers)
        except httpx.TransportError as e:
            raise WhatsAppError(0, type(e).__name__, retryable=True) from e
        self._raise_for(resp)
        result: dict[str, Any] = resp.json()
        return result

    @staticmethod
    def _raise_for(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            retryable = resp.status_code == 429 or resp.status_code >= 500
            raise WhatsAppError(resp.status_code, resp.text[:300], retryable=retryable)
