"""What a turn sends back: 1-2 text bubbles, optionally with reply buttons on the last."""

from dataclasses import dataclass, field
from typing import Any

MAX_BUTTONS = 3
MAX_BUTTON_TITLE = 20
MAX_BUTTON_BODY = 1024


@dataclass(frozen=True)
class Button:
    id: str
    title: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.title) <= MAX_BUTTON_TITLE:
            raise ValueError(f"button title must be 1-{MAX_BUTTON_TITLE} chars: {self.title!r}")


@dataclass(frozen=True)
class Reply:
    bubbles: list[str]
    buttons: list[Button] = field(default_factory=list)
    kind: str = "reply"  # e.g. "safety", "holding": lets later turns see what was sent
    alert_escalation_id: str | None = None  # the turn worker queues a team alert for it
    voice_language: str | None = None  # set: send as a voice note in this BCP-47 language
    # A list or checkout card sent in place of the last bubble (its body is that bubble).
    interactive: dict[str, Any] | None = None
    # Background jobs the turn worker queues after the reply (e.g. a data export).
    tasks: tuple[dict[str, Any], ...] = ()
    # An order's reference_id: the turn worker starts checking it with Razorpay (a payment
    # link has no WhatsApp payment webhook to prompt the check).
    payment_check: str | None = None

    def __post_init__(self) -> None:
        if len(self.buttons) > MAX_BUTTONS:
            raise ValueError("WhatsApp allows at most 3 reply buttons")
        if self.buttons and not self.bubbles:
            raise ValueError("buttons need a bubble to attach to")
        if self.buttons and len(self.bubbles[-1]) > MAX_BUTTON_BODY:
            raise ValueError("button message body too long")
        if self.interactive and (self.buttons or not self.bubbles):
            raise ValueError("an interactive message replaces the last bubble; no buttons")

    def to_meta(self) -> dict[str, Any]:
        """Exact bubbles and buttons, so a redelivered turn resends the same messages."""
        return {
            "bubbles": list(self.bubbles),
            "buttons": [{"id": b.id, "title": b.title} for b in self.buttons],
            "kind": self.kind,
            "alert": self.alert_escalation_id,
            "voice": self.voice_language,
            "interactive": self.interactive,
            "tasks": list(self.tasks),
            "payment_check": self.payment_check,
        }

    @property
    def body(self) -> str:
        return "\n\n".join(self.bubbles)

    @classmethod
    def from_stored(cls, body: str, meta: dict[str, Any] | None) -> "Reply":
        meta = meta or {}
        bubbles = meta.get("bubbles") or ([body] if body else [])
        buttons = [Button(b["id"], b["title"]) for b in meta.get("buttons", [])]
        return cls(
            list(bubbles),
            buttons,
            meta.get("kind", "reply"),
            meta.get("alert"),
            meta.get("voice"),
            meta.get("interactive"),
            tuple(meta.get("tasks") or ()),
            meta.get("payment_check"),
        )
