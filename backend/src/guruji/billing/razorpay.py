"""Razorpay: the source of truth for whether an order was paid.

Webhooks only tell us to look; crediting happens after `check()` confirms with
Razorpay's API that the order is paid in full. Two checkouts, both keyed by our
reference_id:
- WhatsApp's native "Review and pay" card creates a Razorpay order with receipt =
  reference_id;
- a Razorpay payment link (PAYMENT_CHECKOUT=link: no WhatsApp payment configuration
  needed, works with test keys before KYC) carries reference_id as its own.
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
        r = await self._request("GET", path, params=params)
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:200])
        items = r.json().get("items")
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            return await self._http.request(
                method, f"{self._base}{path}", params=params, json=json, auth=self._auth
            )
        except httpx.TransportError as e:
            raise RazorpayError(0, type(e).__name__) from e

    async def _link(self, reference_id: str) -> dict[str, Any] | None:
        r = await self._request("GET", "/v1/payment_links", params={"reference_id": reference_id})
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:200])
        links = r.json().get("payment_links")
        # filtered client-side too: the reference_id filter is not relied on
        found = [
            x
            for x in (links if isinstance(links, list) else [])
            if isinstance(x, dict) and x.get("reference_id") == reference_id
        ]
        return found[0] if found else None

    async def create_link(
        self, reference_id: str, amount_paise: int, description: str, expire_by: int
    ) -> str:
        """A payment link's short URL; asking twice for one reference_id returns the same
        link (Razorpay refuses a duplicate reference_id, so the existing one is fetched)."""
        body = {
            "amount": amount_paise,
            "currency": "INR",
            "accept_partial": False,
            "reference_id": reference_id,
            "description": description[:2048],
            "expire_by": expire_by,
            # no customer details: Razorpay needn't know who the user is
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": {"reference_id": reference_id},
        }
        r = await self._request("POST", "/v1/payment_links", json=body)
        if r.status_code < 400:
            return str(r.json()["short_url"])
        if r.status_code == 400:
            existing = await self._link(reference_id)
            if existing and existing.get("short_url"):
                return str(existing["short_url"])
        raise RazorpayError(r.status_code, r.text[:200])

    async def check(self, reference_id: str, amount_paise: int) -> PaymentState:
        orders = await self._get("/v1/orders", receipt=reference_id)
        if not orders:
            return await self._check_link(reference_id, amount_paise)
        payments = await self._get(f"/v1/orders/{orders[0]['id']}/payments")
        captured = [p for p in payments if p.get("status") == "captured"]
        paid = sum(int(p.get("amount", 0)) for p in captured)
        if captured and paid >= amount_paise:
            return PaymentState("paid", str(captured[0]["id"]), paid)
        if payments and all(p.get("status") == "failed" for p in payments):
            return PaymentState("failed")
        return PaymentState("pending", amount_paise=paid)

    async def _check_link(self, reference_id: str, amount_paise: int) -> PaymentState:
        link = await self._link(reference_id)
        if link is None:
            return PaymentState("pending")  # neither exists yet at Razorpay
        payments = [p for p in link.get("payments") or [] if isinstance(p, dict)]
        captured = [p for p in payments if p.get("status") == "captured"]
        paid = sum(int(p.get("amount", 0)) for p in captured)
        if captured and paid >= amount_paise:
            return PaymentState("paid", str(captured[0].get("payment_id")), paid)
        if link.get("status") in ("expired", "cancelled"):
            return PaymentState("failed")
        # A failed attempt on a link can be retried on the same link: still pending.
        return PaymentState("pending", amount_paise=paid)
