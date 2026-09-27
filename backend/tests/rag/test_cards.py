import re

from guruji.rag.cards import TOPICS, load_cards
from guruji.rag.factors import known_keys

# Keys a chart can produce that deliberately have no card: impossible or not meaningful
# (nodes have no dignity or retrogression of their own; the Sun cannot be combust) and
# Rahu transits outside its well-known good houses.
UNCARDED = {
    *(
        f"{g}:{s}"
        for g in ("rahu", "ketu")
        for s in ("exalted", "debilitated", "combust", "retrograde")
    ),
    "sun:combust",
    "sun:retrograde",
    "moon:retrograde",
    *(f"transit:rahu-from-moon-{h}" for h in (1, 2, 4, 5, 7, 8, 9, 10, 12)),
}
# Guardrails from the persona apply to the corpus too.
FORBIDDEN = re.compile(
    r"\b(death|die|dies|dying|lifespan|longevity|short life|fatal|guarantee[sd]?|"
    r"definitely|certainly will|cure[sd]?|curse[sd]?|doomed)\b",
    re.IGNORECASE,
)


def test_corpus_loads_and_is_complete() -> None:
    cards = load_cards()
    assert len(cards) >= 200
    covered = {f for c in cards for f in c.factors}
    assert covered <= known_keys()
    assert known_keys() - covered == UNCARDED


def test_every_house_placement_has_its_own_card() -> None:
    by_factor = {f: c for c in load_cards() for f in c.factors}
    for g in ("sun", "moon", "mars", "mercury", "jupiter", "venus", "saturn", "rahu", "ketu"):
        for h in range(1, 13):
            card = by_factor[f"{g}:house-{h}"]
            assert card.id == f"{g}-house-{h}"
            assert card.topics  # derived from the house


def test_cards_respect_guardrails() -> None:
    for c in load_cards():
        hit = FORBIDDEN.search(c.text) or FORBIDDEN.search(c.title)
        assert hit is None, f"{c.id}: {hit.group(0) if hit else ''}"
        assert set(c.topics) <= TOPICS


def test_general_cards_exist_for_common_topics() -> None:
    general = [c for c in load_cards() if not c.factors]
    topics = {t for c in general for t in c.topics}
    assert {"marriage", "career", "health", "remedies", "timing", "children"} <= topics
