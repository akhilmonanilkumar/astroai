"""Personal data that lives in Redis rather than Postgres, and how it is forgotten.

Jobs carry phone numbers (and turn jobs the message text) while they are queued. Live
queues drain within seconds, but dead letters (`q:dead`) stay until someone looks, so
they are removed when a user is erased and trimmed after `retention.dead_letter_days`.
"""

import time
from typing import cast

from redis.asyncio import Redis

from guruji.queue.streams import DEAD_STREAM

_PAGE = 500


async def forget_user(redis: Redis, wa_id: str, user_id: str | None = None) -> int:
    """After an erasure: drop the user's dead-lettered jobs and the keys named after their
    phone number. Returns how many dead letters were removed."""
    await redis.delete(f"enq:capi:lead:{wa_id}")
    needles = [wa_id, *([user_id] if user_id else [])]
    removed = 0
    start = "-"
    while True:
        page = cast(
            list[tuple[str, dict[str, str]]],
            await redis.xrange(DEAD_STREAM, min=start, max="+", count=_PAGE),
        )
        doomed = [i for i, f in page if any(n in f.get("job", "") for n in needles)]
        if doomed:
            removed += int(await redis.xdel(DEAD_STREAM, *doomed))
        if len(page) < _PAGE:
            return removed
        start = "(" + page[-1][0]


async def trim_dead_letters(redis: Redis, older_than_days: int) -> int:
    """Remove dead letters older than this many days. Returns how many."""
    cutoff_ms = int((time.time() - older_than_days * 86400) * 1000)
    return int(await redis.xtrim(DEAD_STREAM, minid=f"{cutoff_ms}-0"))
