"""Redis token locks (per-user turn lock, per-user send lock)."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def turn_lock_key(wa_id: str) -> str:
    return f"lock:turn:{wa_id}"


def send_lock_key(wa_id: str) -> str:
    return f"lock:send:{wa_id}"


@asynccontextmanager
async def try_lock(redis: Redis, key: str, ttl_seconds: int) -> AsyncIterator[bool]:
    """Yield True if the lock was acquired (and release it on exit), else False."""
    token = uuid.uuid4().hex
    acquired = bool(await redis.set(key, token, nx=True, ex=ttl_seconds))
    try:
        yield acquired
    finally:
        if acquired:
            await redis.eval(_RELEASE, 1, key, token)
