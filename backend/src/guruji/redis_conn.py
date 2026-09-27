"""Redis connection factory."""

from typing import Any

from redis.asyncio import Redis

_memory_server: Any = None


def make_redis(url: str) -> "Redis":
    """Return an asyncio Redis client with str responses.

    `memory://` gives a fakeredis client sharing one in-process server, so the
    single-process `dev` role works without Docker. fakeredis is a dev dependency.
    """
    if url.startswith("memory://"):
        global _memory_server
        import fakeredis
        from fakeredis.aioredis import FakeRedis

        if _memory_server is None:
            _memory_server = fakeredis.FakeServer()
        client: Redis = FakeRedis(server=_memory_server, decode_responses=True)
        return client
    return Redis.from_url(url, decode_responses=True)
