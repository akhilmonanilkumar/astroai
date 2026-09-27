"""Rule cards: short, original statements of traditional jyotish principles.

The corpus lives in `rag/corpus/*.toml`, reviewed in git like code. Each card says which
chart factors it speaks to (keys from guruji.rag.factors) and which life topics. Cards
with no factors are general method (timing, remedies) and are found by relevance alone.

    [[card]]
    id = "saturn-house-7"
    title = "Saturn in the 7th house"
    factors = ["saturn:house-7"]
    topics = ["marriage", "partnership"]
    text = "..."
"""

import hashlib
import tomllib
from dataclasses import dataclass
from functools import cache
from importlib import resources

from guruji.rag.factors import known_keys

TOPICS = frozenset(
    {
        "self",
        "health",
        "wealth",
        "family",
        "speech",
        "siblings",
        "courage",
        "communication",
        "home",
        "mother",
        "property",
        "education",
        "children",
        "romance",
        "creativity",
        "work",
        "debts",
        "enemies",
        "marriage",
        "partnership",
        "transformation",
        "inheritance",
        "father",
        "luck",
        "dharma",
        "travel",
        "career",
        "status",
        "gains",
        "income",
        "friends",
        "expenses",
        "foreign",
        "spirituality",
        "sleep",
        "mind",
        "emotions",
        "timing",
        "remedies",
        "personality",
        "relationship",
        "finance",
    }
)

HOUSE_TOPICS: dict[int, tuple[str, ...]] = {
    1: ("self", "personality", "health"),
    2: ("wealth", "family", "speech"),
    3: ("courage", "siblings", "communication"),
    4: ("home", "mother", "property", "education"),
    5: ("children", "education", "romance", "creativity"),
    6: ("health", "work", "debts", "enemies"),
    7: ("marriage", "partnership", "relationship"),
    8: ("transformation", "inheritance"),
    9: ("luck", "father", "dharma", "travel"),
    10: ("career", "status", "work"),
    11: ("gains", "income", "friends"),
    12: ("expenses", "foreign", "spirituality", "sleep"),
}


class CardError(ValueError):
    pass


@dataclass(frozen=True)
class Card:
    id: str
    title: str
    text: str
    factors: tuple[str, ...]
    topics: tuple[str, ...]

    @property
    def search_text(self) -> str:
        """What gets embedded and keyword-matched."""
        return f"{self.title}. Topics: {', '.join(self.topics)}. {self.text}"

    @property
    def content_hash(self) -> str:
        raw = "\x1f".join([self.title, self.text, *self.factors, "|", *self.topics])
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _card(raw: dict[str, object], source: str) -> Card:
    try:
        card_id = str(raw["id"])
        factors = tuple(str(f) for f in raw.get("factors", []))  # type: ignore[attr-defined]
        topics = list(str(t) for t in raw.get("topics", []))  # type: ignore[attr-defined]
        card = Card(card_id, str(raw["title"]), " ".join(str(raw["text"]).split()), factors, ())
    except KeyError as e:
        raise CardError(f"{source}: card missing {e}") from e
    for f in factors:
        _, _, value = f.partition(":")
        if value.startswith("house-") and value[6:].isdigit() and int(value[6:]) in HOUSE_TOPICS:
            topics.extend(t for t in HOUSE_TOPICS[int(value[6:])] if t not in topics)
    bad_factors = set(factors) - known_keys()
    bad_topics = set(topics) - TOPICS
    if bad_factors or bad_topics:
        raise CardError(f"{source}:{card_id}: unknown {bad_factors or bad_topics}")
    if not 40 <= len(card.text) <= 900:
        raise CardError(f"{source}:{card_id}: text should be 40-900 characters")
    return Card(card.id, card.title, card.text, factors, tuple(topics))


@cache
def load_cards() -> tuple[Card, ...]:
    """All cards in the corpus, validated. Raises CardError on any bad card."""
    cards: list[Card] = []
    root = resources.files("guruji.rag").joinpath("corpus")
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".toml"):
            continue
        data = tomllib.loads(entry.read_text("utf-8"))
        cards.extend(_card(raw, entry.name) for raw in data.get("card", []))
    ids = [c.id for c in cards]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise CardError(f"duplicate card ids: {sorted(dupes)}")
    return tuple(cards)
