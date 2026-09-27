"""Card search: hybrid (vector + keyword) ranking, restricted to a chart's factors.

Two implementations of one contract: `MemoryCardIndex` (dev/tests; numpy) and
`PostgresCardIndex` (pgvector + full-text). Rankings are fused with reciprocal rank
fusion (RRF), so neither signal needs calibrating against the other.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from guruji.rag.cards import Card
from guruji.rag.embed import Embedder, Vectors

RRF_K = 60
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "the a an of in on to and or is are was be for with your you my me i it this that "
    "from at by as can will when what how which who".split()
)


_STEM = 5  # crude prefix stemming: married/marriage, settle/settling, finance/financial


def tokens(text: str) -> list[str]:
    return [t[:_STEM] for t in _TOKEN.findall(text.casefold()) if t not in _STOP and len(t) > 1]


@dataclass(frozen=True)
class Hit:
    card: Card
    score: float  # fused RRF score (ranking only)
    similarity: float  # cosine similarity to the query, -1..1


class CardIndex(Protocol):
    async def search(
        self,
        query_vec: Vectors,
        query_text: str,
        factors: set[str] | None,
        limit: int,
    ) -> list[Hit]:
        """Best cards for the query. factors=None searches general (factor-less) cards;
        otherwise only cards sharing at least one factor with the set."""
        ...

    async def by_factors(self, factors: set[str]) -> list[Card]: ...


def _rrf(ranks: list[int | None]) -> float:
    return sum(1.0 / (RRF_K + r) for r in ranks if r is not None)


class MemoryCardIndex:
    def __init__(self, cards: tuple[Card, ...] | list[Card], embedder: Embedder) -> None:
        self.cards = list(cards)
        self.vectors = embedder.embed([c.search_text for c in self.cards])
        self._tokens = [Counter(tokens(c.search_text)) for c in self.cards]
        df = Counter(t for toks in self._tokens for t in toks)
        n = len(self.cards)
        self._idf = {t: math.log(1 + n / f) for t, f in df.items()}

    def _candidates(self, factors: set[str] | None) -> list[int]:
        if factors is None:
            return [i for i, c in enumerate(self.cards) if not c.factors]
        return [i for i, c in enumerate(self.cards) if factors.intersection(c.factors)]

    async def search(
        self, query_vec: Vectors, query_text: str, factors: set[str] | None, limit: int
    ) -> list[Hit]:
        idx = self._candidates(factors)
        if not idx:
            return []
        sims = self.vectors[idx] @ query_vec.reshape(-1)
        q = set(tokens(query_text))
        kw = [sum(self._idf.get(t, 0.0) for t in q if t in self._tokens[i]) for i in idx]
        v_rank = {i: r for r, i in enumerate(np.argsort(-sims), start=1)}
        kw_order = [j for j in sorted(range(len(idx)), key=lambda j: -kw[j]) if kw[j] > 0]
        k_rank = {j: r for r, j in enumerate(kw_order, start=1)}
        hits = [
            Hit(self.cards[i], _rrf([v_rank[j], k_rank.get(j)]), float(sims[j]))
            for j, i in enumerate(idx)
        ]
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    async def by_factors(self, factors: set[str]) -> list[Card]:
        return [c for c in self.cards if factors.intersection(c.factors)]


def vector_literal(v: Vectors) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in np.asarray(v).reshape(-1)) + "]"


def _row_card(r: dict[str, Any]) -> Card:
    return Card(r["id"], r["title"], r["body"], tuple(r["factors"]), tuple(r["topics"]))


_SEARCH_SQL = """
with c as (
  select id, title, body, factors, topics,
         embedding <=> %(q)s::vector as dist,
         ts_rank(tsv, websearch_to_tsquery('english', %(t)s)) as kw
  from rule_cards
  where case when %(general)s then cardinality(factors) = 0 else factors && %(f)s end
),
v as (select id, row_number() over (order by dist) as rv from c),
k as (select id, row_number() over (order by kw desc) as rk from c where kw > 0)
select c.*, 1.0 / (%(rrf)s + v.rv) + coalesce(1.0 / (%(rrf)s + k.rk), 0) as score
from c join v using (id) left join k using (id)
order by score desc
limit %(n)s
"""


class PostgresCardIndex:
    """rule_cards in Postgres (pgvector). Filled by `sync_cards`."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def search(
        self, query_vec: Vectors, query_text: str, factors: set[str] | None, limit: int
    ) -> list[Hit]:
        params = {
            "q": vector_literal(query_vec),
            "t": query_text,
            "general": factors is None,
            "f": sorted(factors or []),
            "rrf": RRF_K,
            "n": limit,
        }
        async with self._pool.connection() as conn:
            cur = await conn.execute(_SEARCH_SQL, params)
            rows = await cur.fetchall()
        return [Hit(_row_card(r), float(r["score"]), 1.0 - float(r["dist"])) for r in rows]

    async def by_factors(self, factors: set[str]) -> list[Card]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select id, title, body, factors, topics from rule_cards "
                "where factors && %s order by id",
                (sorted(factors),),
            )
            return [_row_card(r) for r in await cur.fetchall()]


_SYNC_LOCK = 7_700_042  # pg_advisory_xact_lock key: one worker syncs at a time


async def sync_cards(pool: Any, cards: tuple[Card, ...], embedder: Embedder) -> int:
    """Upsert changed cards (by content hash + model) and delete removed ones.

    Idempotent and cheap when nothing changed; returns the number of cards written.
    """
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("select pg_advisory_xact_lock(%s)", (_SYNC_LOCK,))
        cur = await conn.execute("select id, content_hash, model from rule_cards")
        have = {r["id"]: (r["content_hash"], r["model"]) for r in await cur.fetchall()}
        todo = [c for c in cards if have.get(c.id) != (c.content_hash, embedder.name)]
        if todo:
            vecs = embedder.embed([c.search_text for c in todo])
            for c, v in zip(todo, vecs, strict=True):
                await conn.execute(
                    "insert into rule_cards (id, title, body, factors, topics, content_hash, "
                    "model, embedding) values (%s, %s, %s, %s, %s, %s, %s, %s::vector) "
                    "on conflict (id) do update set title = excluded.title, "
                    "body = excluded.body, factors = excluded.factors, "
                    "topics = excluded.topics, content_hash = excluded.content_hash, "
                    "model = excluded.model, embedding = excluded.embedding, updated_at = now()",
                    (
                        c.id,
                        c.title,
                        c.text,
                        list(c.factors),
                        list(c.topics),
                        c.content_hash,
                        embedder.name,
                        vector_literal(v),
                    ),
                )
        gone = sorted(set(have) - {c.id for c in cards})
        if gone:
            await conn.execute("delete from rule_cards where id = any(%s)", (gone,))
    return len(todo)
