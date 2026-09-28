"""Inbound webhook payload models and normalisation.

Only the fields we use are modelled; everything else is kept via `extra="allow"`.
Reference: WhatsApp Cloud API webhook `messages` field.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Profile(_Model):
    name: str | None = None


class Contact(_Model):
    wa_id: str
    profile: Profile | None = None


class Text(_Model):
    body: str


class ReplyRef(_Model):
    id: str
    title: str
    description: str | None = None


class Interactive(_Model):
    type: str
    button_reply: ReplyRef | None = None
    list_reply: ReplyRef | None = None


class QuickReplyButton(_Model):
    payload: str | None = None
    text: str


class Reaction(_Model):
    message_id: str
    emoji: str | None = None  # absent when a reaction is removed


class Media(_Model):
    id: str
    mime_type: str | None = None


class InboundMessage(_Model):
    from_: str = Field(alias="from")
    id: str
    timestamp: str
    type: str
    text: Text | None = None
    interactive: Interactive | None = None
    button: QuickReplyButton | None = None
    audio: Media | None = None
    reaction: Reaction | None = None
    referral: dict[str, Any] | None = None


class Metadata(_Model):
    display_phone_number: str | None = None
    phone_number_id: str


class ChangeValue(_Model):
    messaging_product: str | None = None
    metadata: Metadata | None = None
    contacts: list[Contact] = []
    messages: list[InboundMessage] = []
    statuses: list[dict[str, Any]] = []


class Change(_Model):
    field: str
    value: ChangeValue


class Entry(_Model):
    id: str
    changes: list[Change] = []


class WebhookPayload(_Model):
    object: str
    entry: list[Entry] = []


MessageKind = Literal["text", "reply", "audio", "reaction", "unsupported"]

_WA_ID_RE = re.compile(r"^\d{6,20}$")


class IncomingMessage(BaseModel):
    """A normalised inbound user message, as stored in the coalescer burst buffer."""

    wa_id: str
    wamid: str
    ts: int
    kind: MessageKind
    text: str = ""
    reply_id: str | None = None  # button id; for a reaction, the turn it reacts to
    reacted_to: str | None = None  # a reaction's target message (our wamid)
    media_id: str | None = None
    profile_name: str | None = None
    referral: dict[str, Any] | None = None


def extract_messages(payload: WebhookPayload) -> list[IncomingMessage]:
    out: list[IncomingMessage] = []
    for entry in payload.entry:
        for change in entry.changes:
            if change.field != "messages":
                continue
            names = {
                c.wa_id: (c.profile.name if c.profile else None) for c in change.value.contacts
            }
            for m in change.value.messages:
                if not _WA_ID_RE.match(m.from_):
                    continue  # never let a malformed ID into Redis keys
                out.append(_normalise(m, names.get(m.from_)))
    return out


def _normalise(m: InboundMessage, profile_name: str | None) -> IncomingMessage:
    base: dict[str, Any] = {
        "wa_id": m.from_,
        "wamid": m.id,
        "ts": int(m.timestamp),
        "profile_name": profile_name,
        "referral": m.referral,
    }
    if m.type == "text" and m.text:
        return IncomingMessage(kind="text", text=m.text.body, **base)
    if m.type == "interactive" and m.interactive:
        ref = m.interactive.button_reply or m.interactive.list_reply
        if ref:
            return IncomingMessage(kind="reply", text=ref.title, reply_id=ref.id, **base)
    if m.type == "button" and m.button:
        return IncomingMessage(kind="reply", text=m.button.text, reply_id=m.button.payload, **base)
    if m.type == "reaction" and m.reaction:
        return IncomingMessage(
            kind="reaction", text=m.reaction.emoji or "", reacted_to=m.reaction.message_id, **base
        )
    if m.type == "audio" and m.audio:
        return IncomingMessage(kind="audio", media_id=m.audio.id, **base)
    return IncomingMessage(kind="unsupported", **base)
