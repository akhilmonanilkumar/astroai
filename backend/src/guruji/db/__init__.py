"""Persistence. The app talks to a `Store`; Postgres in staging/prod, memory in dev/test."""

from guruji.db.store import MemoryStore, Store


async def open_store(database_url: str, pool_max: int = 5) -> Store:
    if database_url.startswith("memory://"):
        return MemoryStore()
    from guruji.db.postgres import PostgresStore

    return await PostgresStore.connect(database_url, pool_max)


__all__ = ["MemoryStore", "Store", "open_store"]
