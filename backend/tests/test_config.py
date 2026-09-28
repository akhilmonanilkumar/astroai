"""Settings outside dev refuse anything that belongs to the simulator or to dev."""

from typing import Any

import pytest

from guruji.config import Settings

STAGING: dict[str, Any] = {
    "env": "staging",
    "redis_url": "redis://redis:6379/0",
    "database_url": "postgresql://u:p@pooler.supabase.com:6543/postgres",
    "wa_verify_token": "verify",
    "wa_app_secret": "secret",
    "wa_access_token": "token",
    "wa_phone_number_id": "123",
    "graph_api_base": "https://graph.facebook.com",
    "field_encryption_key": "Ag" + "A" * 41 + "=",
    "lookup_hmac_key": "Aw" + "A" * 41 + "=",
    "guru_model": "sarvam:sarvam-105b",
    "talk_model": "sarvam:sarvam-105b-conversations",
    "fast_model": "sarvam:sarvam-105b-conversations",
    "admin_auth": "supabase",
    "razorpay_key_id": "rzp_test_abc",
    "razorpay_api_base": "https://api.razorpay.com",
    "payment_checkout": "link",
    "razorpay_webhook_secret": "whsec-real",
    "sarvam_api_key": "sk-real",
    "supabase_url": "https://abc.supabase.co",
    "privacy_notice_url": "https://guruji.in/privacy",
    "admin_console_url": "https://admin.guruji.in",
}


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **{**STAGING, **overrides})  # type: ignore[call-arg]


def test_a_complete_staging_config_is_accepted() -> None:
    s = _settings()
    assert s.payment_checkout == "link" and s.razorpay_key_id.startswith("rzp_test_")


@pytest.mark.parametrize(
    ("override", "why"),
    [
        ({"graph_api_base": "http://127.0.0.1:8100"}, "GRAPH_API_BASE"),
        ({"graph_api_base": "http://simulator:8100"}, "GRAPH_API_BASE"),
        ({"razorpay_api_base": "http://localhost:8100/razorpay"}, "simulator payment"),
        ({"razorpay_key_id": "rzp_test_simulator"}, "simulator payment"),
        ({"wa_app_secret": "dev-app-secret"}, "dev WhatsApp secrets"),
        ({"database_url": "memory://"}, "memory:// database"),
        ({"razorpay_webhook_secret": "sim-webhook-secret"}, "RAZORPAY_WEBHOOK_SECRET"),
        ({"payment_checkout": "whatsapp"}, "WA_PAYMENT_CONFIG"),
        ({"privacy_notice_url": "https://guruji.example/privacy"}, "PRIVACY_NOTICE_URL"),
        ({"admin_console_url": "https://admin.guruji.example"}, "ADMIN_CONSOLE_URL"),
        ({"sarvam_api_key": None}, "SARVAM_API_KEY"),
        ({"telegram_bot_token": "123:abc"}, "TELEGRAM_WEBHOOK_SECRET"),
        ({"supabase_url": None}, "SUPABASE_URL"),
    ],
)
def test_simulator_and_dev_values_are_refused(override: dict[str, Any], why: str) -> None:
    with pytest.raises(ValueError, match=why):
        _settings(**override)
