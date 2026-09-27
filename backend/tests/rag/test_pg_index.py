"""PostgresCardIndex + sync_cards against real Postgres with pgvector (TEST_DATABASE_URL)."""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from guruji.rag.cards import Card, load_cards
from guruji.rag.embed import HashEmbedder
from guruji.rag.index import MemoryCardIndex, PostgresCardIndex, sync_cards

PG_URL = os.environ.get("TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).parents[3] / "supabase" / "migrations"
pytestmark = pytest.mark.skipif(not PG_URL, reason="set TEST_DATABASE_URL (Postgres + pgvector)")


@pytest.fixture
async def pool() -> AsyncIterator[Any]:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    assert PG_URL
    async with await psycopg.AsyncConnection.connect(PG_URL, autocommit=True) as conn:
        await conn.execute("drop schema if exists public cascade; create schema public;")
        for f in sorted(MIGRATIONS.glob("*.sql")):
            await conn.execute(f.read_text("utf-8"))  # type: ignore[arg-type]
    p: Any = AsyncConnectionPool(
        PG_URL,
        min_size=1,
        max_size=2,
        open=False,
        kwargs={"prepare_threshold": None, "row_factory": dict_row},
    )
    await p.open(wait=True)
    yield p
    await p.close()


async def test_sync_is_idempotent_and_prunes(pool: Any) -> None:
    cards = load_cards()
    emb = HashEmbedder()
    assert await sync_cards(pool, cards, emb) == len(cards)
    assert await sync_cards(pool, cards, emb) == 0  # unchanged: nothing re-embedded
    edited = Card(
        cards[0].id, cards[0].title, cards[0].text + " Extra.", cards[0].factors, cards[0].topics
    )
    assert await sync_cards(pool, (edited, *cards[1:-1]), emb) == 1
    async with pool.connection() as conn:
        cur = await conn.execute("select count(*) as n from rule_cards")
        assert (await cur.fetchone())["n"] == len(cards) - 1  # last card deleted


async def test_postgres_search_matches_memory_semantics(pool: Any) -> None:
    cards = load_cards()
    emb = HashEmbedder()
    await sync_cards(pool, cards, emb)
    pg, mem = PostgresCardIndex(pool), MemoryCardIndex(cards, emb)
    factors = {"saturn:house-7", "venus:house-7", "mahadasha:venus", "lagna:libra"}
    q = "marriage spouse partnership timing"
    vec = emb.embed([q])[0]
    pg_hits = await pg.search(vec, q, factors, 4)
    mem_hits = await mem.search(vec, q, factors, 4)
    assert {h.card.id for h in pg_hits} <= {c.id for c in cards if set(c.factors) & factors}
    assert pg_hits[0].card.id == mem_hits[0].card.id
    general = await pg.search(vec, q, None, 3)
    assert general and all(not h.card.factors for h in general)
    assert {c.id for c in await pg.by_factors({"lagna:libra"})} == {"lagna-libra"}
