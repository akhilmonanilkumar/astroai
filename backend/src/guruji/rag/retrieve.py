"""Which rule cards the guru sees on a turn.

Factor first: a card is only a candidate if this chart has one of its factors, so the
guru never reads "Saturn in the 7th" to someone whose Saturn is elsewhere. Among those,
cards are ranked by relevance to the question. A couple of general method cards
(timing, remedies) are added when they are close to the question.
"""

import asyncio

from guruji.rag.cards import Card
from guruji.rag.embed import Embedder
from guruji.rag.glossary import expand
from guruji.rag.index import CardIndex

CHART_CARDS = 5
GENERAL_CARDS = 2
GENERAL_MIN_SIMILARITY = 0.35

# The first reading, with no question yet: the chart's core, most important first.
_CORE_ORDER = ("lagna:", "moon:", "moon-nakshatra:", "mahadasha:", "antardasha:", "yoga:")
CORE_CARDS = 6


class Retriever:
    def __init__(self, index: CardIndex, embedder: Embedder) -> None:
        self.index = index
        self.embedder = embedder

    async def for_question(self, question: str, factors: set[str]) -> list[Card]:
        query = expand(question)
        vec = (await asyncio.to_thread(self.embedder.embed, [query]))[0]
        chart = await self.index.search(vec, query, factors, CHART_CARDS) if factors else []
        general = [
            h
            for h in await self.index.search(vec, query, None, GENERAL_CARDS)
            if h.similarity >= GENERAL_MIN_SIMILARITY
        ]
        return [h.card for h in chart] + [h.card for h in general]

    async def core(self, factors: set[str]) -> list[Card]:
        cards = await self.index.by_factors(factors)

        def rank(c: Card) -> int:
            hits = [
                i
                for i, prefix in enumerate(_CORE_ORDER)
                for f in c.factors
                if f in factors and f.startswith(prefix)
            ]
            return min(hits, default=len(_CORE_ORDER))

        ranked = sorted((c for c in cards if rank(c) < len(_CORE_ORDER)), key=rank)
        return ranked[:CORE_CARDS]
