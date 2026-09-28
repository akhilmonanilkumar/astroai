"""Razorpay: the source of truth for whether an order was paid.

Webhooks only tell us to look; crediting happens after `check()` confirms with
Razorpay's API that the order (receipt = our reference_id) is paid in full.
"""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Literal

import httpx


@dataclass(frozen=True)
class PaymentState:
    status: Literal["paid", "pending", "failed"]
    payment_id: str | None = None
    amount_paise: int = 0


def valid_webhook(body: bytes, signature: str, secret: str) -> bool:
    """X-Razorpay-Signature: hex HMAC-SHA256 of the raw body with the webhook secret."""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class RazorpayError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"razorpay {status}: {detail}")
        self.retryable = status == 0 or status == 429 or status >= 500


class Razorpay:
    def __init__(
        self, key_id: str, key_secret: str, base: str, http: httpx.AsyncClient | None = None
    ) -> None:
        self._auth = (key_id, key_secret)
        self._base = base.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=15.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, **params: str) -> list[dict[str, Any]]:
        """The "items" of a Razorpay collection response."""
        try:
            r = await self._http.get(f"{self._base}{path}", params=params, auth=self._auth)
        except httpx.TransportError as e:
            raise RazorpayError(0, type(e).__name__) from e
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:200])
        items = r.json().get("items")
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []

    async def check(self, reference_id: str, amount_paise: int) -> PaymentState:
        orders = await self._get("/v1/orders", receipt=reference_id)
        if not orders:
            return PaymentState("pending")  # the order may not exist yet at Razorpay
        payments = await self._get(f"/v1/orders/{orders[0]['id']}/payments")
        captured = [p for p in payments if p.get("status") == "captured"]
        paid = sum(int(p.get("amount", 0)) for p in captured)
        if captured and paid >= amount_paise:
            return PaymentState("paid", str(captured[0]["id"]), paid)
        if payments and all(p.get("status") == "failed" for p in payments):
            return PaymentState("failed")
        return PaymentState("pending", amount_paise=paid)
