from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from guruji.astro import BirthInput, Sky, compute_dossier, transit_snapshot
from guruji.geo.tz import birth_moment
from guruji.rag.cards import Card, load_cards
from guruji.rag.embed import FastEmbedder, HashEmbedder
from guruji.rag.factors import current_factors, known_keys, natal_factors
from guruji.rag.glossary import expand
from guruji.rag.index import MemoryCardIndex
from guruji.rag.retrieve import Retriever

NOW = datetime(2026, 9, 28, tzinfo=UTC)
MODELS = Path(__file__).parents[2] / "data" / "models"


@pytest.fixture(scope="module")
def dossier_factors(sky: Sky) -> set[str]:
    birth = BirthInput(
        moment=birth_moment(date(1990, 7, 15), time(9, 0), "Asia/Kolkata"),
        latitude=28.6139,
        longitude=77.2090,
    )
    d = compute_dossier(sky, birth, NOW)
    return natal_factors(d) | current_factors(d, NOW, transit_snapshot(sky, d.d1, NOW))


def test_chart_factors(dossier_factors: set[str]) -> None:
    assert {
        "lagna:leo",
        "moon:pisces",
        "moon-nakshatra:revati",
        "saturn:house-5",
        "saturn:retrograde",
        "mars:house-9",
        "yoga:gajakesari",
        "mahadasha:sun",
        "antardasha:jupiter",
        "sade-sati:peak",
        "transit:saturn-from-moon-1",
    } <= dossier_factors
    assert dossier_factors <= known_keys()


def test_glossary_expansion() -> None:
    assert "marriage" in expand("meri shaadi kab hogi")
    assert "career" in expand("नौकरी कब लगेगी")
    assert expand("hello there") == "hello there"


@pytest.fixture(scope="module")
def retriever() -> Retriever:
    return Retriever(MemoryCardIndex(load_cards(), HashEmbedder()), HashEmbedder())


async def test_only_this_charts_cards(retriever: Retriever, dossier_factors: set[str]) -> None:
    cards = await retriever.for_question("marriage spouse partnership", dossier_factors)
    chart_cards = [c for c in cards if c.factors]
    assert chart_cards
    for c in chart_cards:
        assert set(c.factors) & dossier_factors, c.id
    assert "saturn-house-7" not in {c.id for c in cards}  # this Saturn is in the 5th


async def test_general_method_cards_join_when_relevant(
    retriever: Retriever, dossier_factors: set[str]
) -> None:
    cards = await retriever.for_question(
        "how to read marriage timing 7th house venus", dossier_factors
    )
    assert "method-marriage" in {c.id for c in cards}


async def test_first_reading_core_cards(retriever: Retriever, dossier_factors: set[str]) -> None:
    ids = [c.id for c in await retriever.core(dossier_factors)]
    assert ids[:3] == ["lagna-leo", "moon-pisces", "moon-house-8"] or ids[0] == "lagna-leo"
    assert "nak-revati" in ids and len(ids) <= 6


async def test_no_factors_means_general_only(retriever: Retriever) -> None:
    cards = await retriever.for_question("remedies mantra charity", set())
    assert cards and all(not c.factors for c in cards)


# Held-out questions (not used to build the glossary) in English, Hinglish and Hindi.
QUALITY = [
    ("Is my married life going to be happy?", "marriage"),
    ("biwi ke saath jhagde bahut hote hain", "marriage"),
    ("रिश्ता कब पक्का होगा", "marriage"),
    ("Will I get a government job?", "career"),
    ("office mein boss se problem hai", "career"),
    ("क्या मुझे नया काम शुरू करना चाहिए", "career"),
    ("How will my finances be next year?", "wealth"),
    ("karza kab utrega", "wealth"),
    ("When will we have a baby?", "children"),
    ("santan sukh kab milega", "children"),
    ("Can I settle in Canada?", "foreign"),
    ("videsh mein naukri milegi kya", "foreign"),
    ("Which remedy should I do for Saturn?", "remedies"),
    ("shani ke upay batao", "remedies"),
    ("When can I buy a flat?", "property"),
    ("apna ghar kab hoga", "property"),
    ("Will I clear my UPSC exam?", "education"),
    ("padhai mein mann nahi lagta", "education"),
    ("I feel anxious all the time", "mind"),
    ("mann bahut ghabrata hai", "mind"),
]


@pytest.mark.skipif(not MODELS.is_dir(), reason="run `uv run python -m guruji fetch-models` first")
async def test_multilingual_retrieval_quality() -> None:
    """With every card eligible, the top 3 must include one on the asked topic."""
    embedder = FastEmbedder(MODELS)
    retr = Retriever(MemoryCardIndex(load_cards(), embedder), embedder)
    everything = known_keys()
    misses = []
    for question, topic in QUALITY:
        cards: list[Card] = (await retr.for_question(question, everything))[:3]
        if not any(topic in c.topics for c in cards):
            misses.append((question, [c.id for c in cards]))
    assert len(misses) <= 2, misses
