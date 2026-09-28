"""Process settings (secrets, endpoints, tuning) loaded from env / backend/.env.

Business knobs (prices, free limits, feature flags) live in the `app_config` table,
not here, so they can be edited from the admin console without a deploy.
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRETS = {"dev-verify-token", "dev-app-secret", "dev-access-token"}
# Fixed dev/test keys (32 zero / one bytes, base64). Refused outside dev/test.
_DEV_FIELD_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_DEV_LOOKUP_KEY = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    log_level: str = "INFO"

    # "memory://" uses an in-process fake Redis (dev/test only).
    redis_url: str = "redis://localhost:6379/0"

    bind_host: str = "127.0.0.1"
    ingress_port: int = 8000
    simulator_port: int = 8100
    admin_port: int = 8200

    # Admin console API. "supabase": Supabase Auth JWTs with MFA (aal2) for team members in
    # the `admins` table; keys come from SUPABASE_URL (JWKS) or the legacy JWT secret.
    # "dev": the token "dev:<email>" signs in as that admin (dev/test only).
    admin_auth: Literal["dev", "supabase"] = "dev"
    supabase_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None

    # WhatsApp Cloud API
    wa_verify_token: str = "dev-verify-token"
    wa_app_secret: str = "dev-app-secret"
    wa_access_token: str = "dev-access-token"
    wa_phone_number_id: str = "1000000001"
    # Simulator in dev; https://graph.facebook.com in staging/prod.
    graph_api_base: str = "http://127.0.0.1:8100"
    graph_api_version: str = "v23.0"

    # Simulator
    simulator_ingress_url: str = "http://127.0.0.1:8000/webhook"

    # Coalescer: merge bubbles sent within `burst_quiet_seconds` of each other,
    # but never hold the first one longer than `burst_max_seconds`.
    burst_quiet_seconds: float = 2.5
    burst_max_seconds: float = 4.0
    coalescer_tick_seconds: float = 0.2

    # Idempotency / locking
    dedupe_ttl_seconds: int = 7 * 24 * 3600
    turn_lock_seconds: int = 120
    send_lock_seconds: int = 60

    # Workers
    worker_concurrency: int = 50
    job_max_attempts: int = 5

    # Sender pacing between bubbles of one reply
    bubble_min_delay: float = 0.8
    bubble_max_delay: float = 3.0
    bubble_chars_per_second: float = 60.0
    max_bubbles_per_turn: int = 2

    # JPL DE440s for the astro engine (`python -m guruji fetch-ephemeris`)
    ephemeris_path: str = "data/ephemeris/de440s.bsp"
    # GeoNames places for onboarding (`python -m guruji fetch-geonames`)
    geonames_dir: str = "data/geonames"
    # Local embedding model files for rule-card search (`python -m guruji fetch-models`)
    models_dir: str = "data/models"

    # Postgres (Supabase pooler, transaction mode). "memory://" = in-process store (dev/test).
    database_url: str = "memory://"
    db_pool_max: int = 5

    # Personal-data encryption (base64, 32 bytes each); see guruji.crypto
    field_encryption_key: str = _DEV_FIELD_KEY
    lookup_hmac_key: str = _DEV_LOOKUP_KEY

    # LLMs as "provider:model". guru_model does real work (readings, timing, remedies);
    # talk_model handles conversation (greetings, thanks, clarifying); fast_model routes
    # turns and extracts onboarding answers. "fake" = scripted replies, no API calls.
    guru_model: str = "sarvam:sarvam-105b"
    guru_fallback_model: str = "sarvam:sarvam-105b-conversations"
    talk_model: str = "sarvam:sarvam-105b-conversations"
    fast_model: str = "sarvam:sarvam-105b-conversations"
    reasoning_effort: Literal["none", "low", "high", "max"] = "none"
    sarvam_api_key: SecretStr | None = None
    sarvam_base_url: str = "https://api.sarvam.ai/v1"
    # Voice notes (Sarvam speech APIs); speaker is a bulbul:v3 voice
    sarvam_speech_url: str = "https://api.sarvam.ai"
    tts_speaker: str = "aditya"
    voice_enabled: bool = True

    # Payments: Razorpay behind WhatsApp's native checkout (order_details). The payment
    # configuration name is the one set up in WhatsApp Manager. Dev defaults point at the
    # simulator, which also plays Razorpay; they are refused outside dev/test.
    razorpay_key_id: str = "rzp_test_simulator"
    razorpay_key_secret: SecretStr = SecretStr("sim-key-secret")
    razorpay_webhook_secret: SecretStr = SecretStr("sim-webhook-secret")
    razorpay_api_base: str = "http://127.0.0.1:8100/razorpay"
    wa_payment_config: str = "guruji-simulator"
    # "whatsapp": the native order_details card (needs the payment configuration above,
    # i.e. a verified business with Razorpay linked in WhatsApp Manager). "link": a
    # Razorpay payment link behind a URL button; works with test keys, no KYC.
    payment_checkout: Literal["whatsapp", "link"] = "whatsapp"
    payment_check_attempts: int = 8  # a pending payment is re-checked this often

    # Meta Conversions API for Click-to-WhatsApp ads (Lead on onboarding, Purchase on
    # payment). Unset: events are skipped (dev). Only users who came from an ad are sent.
    capi_dataset_id: str | None = None
    capi_access_token: SecretStr | None = None
    wa_business_account_id: str | None = None

    # Escalation alerts (Telegram). Without a bot token, alerts go to the log (dev).
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    # Telegram sends it back in X-Telegram-Bot-Api-Secret-Token on button taps.
    telegram_webhook_secret: SecretStr | None = None
    admin_console_url: str = "https://admin.guruji.example"
    alert_repeat_seconds: int = 300  # re-ping unacknowledged urgent escalations
    alert_check_seconds: float = 30.0
    llm_timeout_seconds: float = 45.0
    history_messages: int = 12

    # Consent notice shown at onboarding; bump the version whenever the text changes.
    privacy_notice_url: str = "https://guruji.example/privacy"
    notice_version: str = "2026-09-v1"

    @model_validator(mode="after")
    def _no_dev_secrets_outside_dev(self) -> Self:
        if self.env in ("staging", "prod"):
            used = {self.wa_verify_token, self.wa_app_secret, self.wa_access_token} & _DEV_SECRETS
            if used:
                raise ValueError("dev WhatsApp secrets must not be used outside dev/test")
            if self.redis_url.startswith("memory://"):
                raise ValueError("memory:// Redis is dev/test only")
            if self.database_url.startswith("memory://"):
                raise ValueError("memory:// database is dev/test only")
            if {self.field_encryption_key, self.lookup_hmac_key} & {
                _DEV_FIELD_KEY,
                _DEV_LOOKUP_KEY,
            }:
                raise ValueError("dev encryption keys must not be used outside dev/test")
            if "fake" in (self.guru_model, self.talk_model, self.fast_model):
                raise ValueError("fake LLMs are dev/test only")
            if self.admin_auth == "dev":
                raise ValueError("dev admin sign-in is dev/test only")
            if self.razorpay_key_id == "rzp_test_simulator" or "127.0.0.1" in (
                self.razorpay_api_base
            ):
                raise ValueError("simulator payment settings are dev/test only")
        return self

    @property
    def graph_messages_url(self) -> str:
        base = self.graph_api_base.rstrip("/")
        return f"{base}/{self.graph_api_version}/{self.wa_phone_number_id}/messages"


@lru_cache
def get_settings() -> Settings:
    return Settings()
