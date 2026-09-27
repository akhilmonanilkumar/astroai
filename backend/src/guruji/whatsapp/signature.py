"""Meta webhook signature (X-Hub-Signature-256) signing and verification."""

import hashlib
import hmac

SIGNATURE_HEADER = "X-Hub-Signature-256"


def sign(body: bytes, app_secret: str) -> str:
    digest = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify(body: bytes, header: str | None, app_secret: str) -> bool:
    if not header:
        return False
    return hmac.compare_digest(sign(body, app_secret), header)
