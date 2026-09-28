"""Who is calling the admin API.

The console signs team members in with Supabase Auth: email + password, then a TOTP code.
Its access token (a JWT) arrives as `Authorization: Bearer ...`. We accept it only when:
the signature, expiry, audience and issuer check out; MFA was completed (`aal2`); and the
email is an enabled row in `admins`. Supabase signs with asymmetric keys published at
`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`; older projects use a shared HS256 secret.

In dev/test, `DevVerifier` accepts "dev:<email>" (an owner) or "dev:<email>:agent" so the
console runs without Supabase; such a team member is created on first sign-in.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import jwt

from guruji.config import Settings
from guruji.db.admin import AdminRole


class AuthError(Exception):
    """The token is missing, invalid, expired, or lacks MFA."""


@dataclass(frozen=True)
class Identity:
    email: str
    mfa: bool
    dev_role: AdminRole | None = None  # dev sign-in: create the team member if missing


class Verifier(Protocol):
    async def verify(self, token: str) -> Identity: ...


class DevVerifier:
    async def verify(self, token: str) -> Identity:
        email, _, role = token.removeprefix("dev:").partition(":")
        if not token.startswith("dev:") or "@" not in email or role not in ("", "agent"):
            raise AuthError("dev sign-in expects the token dev:<email>[:agent]")
        return Identity(email.strip().lower(), mfa=True, dev_role="agent" if role else "owner")


class SupabaseVerifier:
    def __init__(self, supabase_url: str | None, jwt_secret: str | None) -> None:
        if not supabase_url:
            raise ValueError("SUPABASE_URL is required for Supabase admin sign-in")
        base = supabase_url.rstrip("/")
        self.issuer = f"{base}/auth/v1"
        self.secret = jwt_secret
        # Keys are cached by the client; a fetch happens only for an unknown key id.
        self.jwks = None if jwt_secret else jwt.PyJWKClient(f"{self.issuer}/.well-known/jwks.json")

    async def verify(self, token: str) -> Identity:
        try:
            if self.jwks is not None:
                key: Any = (await asyncio.to_thread(self.jwks.get_signing_key_from_jwt, token)).key
                algorithms = ["ES256", "RS256"]
            else:
                key, algorithms = self.secret, ["HS256"]
            claims = jwt.decode(
                token,
                key,
                algorithms=algorithms,
                audience="authenticated",
                issuer=self.issuer,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as e:
            raise AuthError(f"invalid token: {type(e).__name__}") from e
        email = str(claims.get("email") or "").lower()
        if not email:
            raise AuthError("token has no email")
        return Identity(email, mfa=claims.get("aal") == "aal2")


def make_verifier(settings: Settings) -> Verifier:
    if settings.admin_auth == "dev":
        return DevVerifier()
    secret = settings.supabase_jwt_secret
    return SupabaseVerifier(settings.supabase_url, secret.get_secret_value() if secret else None)
