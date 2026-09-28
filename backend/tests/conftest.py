import asyncio
import json
import os
import sys
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis

from guruji.astro import Sky
from guruji.config import Settings
from guruji.db.store import MemoryStore, Store
from guruji.ephemeris import load_sky
from guruji.whatsapp import signature

EPHEMERIS = Path(__file__).parents[1] / "data" / "ephemeris" / "de440s.bsp"
MIGRATIONS = Path(__file__).parents[2] / "supabase" / "migrations"
PG_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def sky() -> Sky:
    if not EPHEMERIS.is_file():
        msg = "JPL ephemeris missing: run `uv run python -m guruji fetch-ephemeris`"
        if os.environ.get("GURUJI_REQUIRE_EPHEMERIS") == "1":
            pytest.fail(msg)
        pytest.skip(msg)
    return load_sky(EPHEMERIS)


if sys.platform == "win32":
    # psycopg's async mode (Postgres store tests) needs a selector loop on Windows. Defined
    # only there: pytest-asyncio rejects a hook that returns None.
    def pytest_asyncio_loop_factories(
        config: pytest.Config, item: pytest.Item
    ) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
        return {"selector": asyncio.SelectorEventLoop}


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        redis_url="memory://",
        graph_api_base="http://graph.test",
        bubble_min_delay=0,
        bubble_max_delay=0,
    )


@pytest.fixture
async def redis() -> AsyncIterator[FakeRedis]:
    client = FakeRedis(server=FakeServer(), decode_responses=True)
    yield client
    await client.aclose()


def webhook(
    wa_id: str = "919800000001",
    wamid: str = "wamid.A",
    text: str = "hello",
    ts: int | None = None,
) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "1000000001"},
                            "contacts": [{"wa_id": wa_id, "profile": {"name": "Asha"}}],
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": wamid,
                                    "timestamp": str(ts or int(time.time())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def signed(payload: dict[str, Any], secret: str) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(payload).encode()
    return raw, {
        "Content-Type": "application/json",
        signature.SIGNATURE_HEADER: signature.sign(raw, secret),
    }


async def _fresh_postgres(url: str) -> Store:
    import psycopg

    from guruji.db.postgres import PostgresStore

    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute("drop schema if exists public cascade; create schema public;")
        for f in sorted(MIGRATIONS.glob("*.sql")):
            await conn.execute(f.read_text("utf-8"))  # type: ignore[arg-type]
    return await PostgresStore.connect(url, max_size=2)


@pytest.fixture(params=["memory", "postgres"])
async def store(request: pytest.FixtureRequest) -> AsyncIterator[Store]:
    """Each store implementation in turn; Postgres only when TEST_DATABASE_URL is set."""
    if request.param == "memory":
        s: Store = MemoryStore()
    elif PG_URL:
        s = await _fresh_postgres(PG_URL)
    else:
        pytest.skip("set TEST_DATABASE_URL to run the Postgres store tests")
    yield s
    await s.aclose()
