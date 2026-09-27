"""Burst merging: bubbles a user sends in quick succession become one turn.

Ingress appends each message to `burst:msgs:<wa_id>` and (re)schedules the burst in the
`burst:due` sorted set at `min(now + quiet, first + max)`. The coalescer loop flushes due
bursts into one `turn` job. A burst is never flushed while that user's turn is running;
it keeps collecting and is flushed as one merged turn when the turn lock is released.

Urgent messages (crisis / emergency signals, see guruji.safety.detect) skip the quiet wait
and flush at once to the `crisis` stream, which turn workers read before ordinary turns.

Both steps are single Lua scripts, so any number of ingress / coalescer processes are safe
and a flushed burst is either fully enqueued or not flushed at all.
"""

import asyncio
import contextlib
import logging
import time
from typing import cast

from redis.asyncio import Redis

from guruji.config import Settings
from guruji.queue.locks import turn_lock_key
from guruji.queue.streams import Queue, enqueue, stream_key
from guruji.safety.detect import looks_urgent
from guruji.whatsapp.models import IncomingMessage

log = logging.getLogger(__name__)

DUE_KEY = "burst:due"
LOCK_RETRY_SECONDS = 0.5


def msgs_key(wa_id: str) -> str:
    return f"burst:msgs:{wa_id}"


def meta_key(wa_id: str) -> str:
    return f"burst:meta:{wa_id}"


# KEYS: msgs, meta, due   ARGV: wa_id, msg_json, now, quiet, max, urgent (1/0)
_INGEST = """
local n = redis.call('RPUSH', KEYS[1], ARGV[2])
local first = redis.call('HGET', KEYS[2], 'first')
if not first then
  first = ARGV[3]
  redis.call('HSET', KEYS[2], 'first', first)
end
local due = math.min(tonumber(ARGV[3]) + tonumber(ARGV[4]), tonumber(first) + tonumber(ARGV[5]))
if ARGV[6] == '1' then
  redis.call('HSET', KEYS[2], 'urgent', '1')
end
if redis.call('HGET', KEYS[2], 'urgent') == '1' then
  due = tonumber(ARGV[3])
end
redis.call('ZADD', KEYS[3], due, ARGV[1])
return n
"""

# KEYS: due, msgs, meta, turn_lock, turn_stream, crisis_stream   ARGV: wa_id, now, lock_retry
# Returns number of messages flushed, 0 if not due / deferred.
_FLUSH = """
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if (not score) or tonumber(score) > tonumber(ARGV[2]) then return 0 end
if redis.call('EXISTS', KEYS[4]) == 1 then
  redis.call('ZADD', KEYS[1], tonumber(ARGV[2]) + tonumber(ARGV[3]), ARGV[1])
  return 0
end
local msgs = redis.call('LRANGE', KEYS[2], 0, -1)
local stream = KEYS[5]
if redis.call('HGET', KEYS[3], 'urgent') == '1' then stream = KEYS[6] end
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('DEL', KEYS[2], KEYS[3])
if #msgs == 0 then return 0 end
local job = '{"kind":"turn","wa_id":"' .. ARGV[1] .. '","messages":['
  .. table.concat(msgs, ',') .. ']}'
redis.call('XADD', stream, '*', 'job', job)
return #msgs
"""


async def ingest(
    redis: Redis, settings: Settings, msg: IncomingMessage, now: float | None = None
) -> None:
    """Buffer one inbound message. The first bubble of a burst also triggers typing dots."""
    now = time.time() if now is None else now
    n = await redis.eval(
        _INGEST,
        3,
        msgs_key(msg.wa_id),
        meta_key(msg.wa_id),
        DUE_KEY,
        msg.wa_id,
        msg.model_dump_json(),
        str(now),
        str(settings.burst_quiet_seconds),
        str(settings.burst_max_seconds),
        "1" if msg.kind == "text" and looks_urgent(msg.text) else "0",
    )
    if int(n) == 1:
        await enqueue(
            redis,
            Queue.SEND,
            {"kind": "typing", "to": msg.wa_id, "message_id": msg.wamid},
            job_id=f"typing:{msg.wamid}",
        )


async def flush_due(redis: Redis, now: float | None = None, limit: int = 500) -> int:
    """Flush every due burst into a turn job. Returns the number of turns enqueued."""
    now = time.time() if now is None else now
    due = cast(list[str], await redis.zrangebyscore(DUE_KEY, "-inf", now, start=0, num=limit))
    turns = 0
    for wa_id in due:
        n = await redis.eval(
            _FLUSH,
            6,
            DUE_KEY,
            msgs_key(wa_id),
            meta_key(wa_id),
            turn_lock_key(wa_id),
            stream_key(Queue.TURN),
            stream_key(Queue.CRISIS),
            wa_id,
            str(now),
            str(LOCK_RETRY_SECONDS),
        )
        if int(n):
            turns += 1
    return turns


async def run_coalescer(redis: Redis, settings: Settings, stop: asyncio.Event) -> None:
    log.info("coalescer started")
    while not stop.is_set():
        try:
            await flush_due(redis)
        except Exception:
            log.exception("coalescer tick failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.coalescer_tick_seconds)
