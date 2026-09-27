from typing import Any

from conftest import webhook
from guruji.whatsapp import signature
from guruji.whatsapp.models import WebhookPayload, extract_messages


def test_signature_roundtrip() -> None:
    sig = signature.sign(b"{}", "s3cret")
    assert signature.verify(b"{}", sig, "s3cret")
    assert not signature.verify(b"{}", sig, "other")
    assert not signature.verify(b"{ }", sig, "s3cret")
    assert not signature.verify(b"{}", None, "s3cret")


def test_extract_text_message() -> None:
    msgs = extract_messages(WebhookPayload.model_validate(webhook(text="namaste")))
    assert len(msgs) == 1
    m = msgs[0]
    assert (m.wa_id, m.wamid, m.kind, m.text, m.profile_name) == (
        "919800000001",
        "wamid.A",
        "text",
        "namaste",
        "Asha",
    )


def _with_message(message: dict[str, Any]) -> WebhookPayload:
    p = webhook()
    p["entry"][0]["changes"][0]["value"]["messages"] = [
        {"from": "919800000001", "id": "wamid.X", "timestamp": "1700000000", **message}
    ]
    return WebhookPayload.model_validate(p)


def test_extract_button_reply() -> None:
    p = _with_message(
        {
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "consent_yes", "title": "I agree"},
            },
        }
    )
    [m] = extract_messages(p)
    assert (m.kind, m.reply_id, m.text) == ("reply", "consent_yes", "I agree")


def test_extract_audio_and_referral() -> None:
    p = _with_message(
        {
            "type": "audio",
            "audio": {"id": "media123", "mime_type": "audio/ogg; codecs=opus"},
            "referral": {"source_type": "ad", "source_id": "ad42", "ctwa_clid": "clid"},
        }
    )
    [m] = extract_messages(p)
    assert (m.kind, m.media_id) == ("audio", "media123")
    assert m.referral and m.referral["source_id"] == "ad42"


def test_status_only_payload_yields_nothing() -> None:
    p = webhook()
    value = p["entry"][0]["changes"][0]["value"]
    value["messages"] = []
    value["statuses"] = [{"id": "wamid.OUT", "status": "delivered"}]
    assert extract_messages(WebhookPayload.model_validate(p)) == []


def test_malformed_wa_id_is_dropped() -> None:
    p = webhook(wa_id="91980:evil")
    assert extract_messages(WebhookPayload.model_validate(p)) == []
