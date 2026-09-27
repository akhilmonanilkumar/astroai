"""Job queues on Redis Streams with consumer groups.

- One stream per queue (`q:<name>`); a worker consumes a list of queues in strict
  priority order (crisis before turn, etc.).
- At-least-once: a job is acked only after its handler returns. Jobs left pending by a
  crashed worker are reclaimed after `claim_idle_ms`.
- Handlers must be idempotent; `enqueue(job_id=...)` dedupes producers.
- Failures retry with exponential backoff (`not_before`), then go to `q:dead`.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import ResponseError

log = logging.getLogger(__name__)

GROUP = "workers"
DEAD_STREAM = "q:dead"


class Queue(StrEnum):
    CRISIS = "crisis"
    TURN = "turn"
    PAYMENT = "payment"
    VOICE = "voice"
    SEND = "send"
    ALERT = "alert"
    BACKGROUND = "background"


def stream_key(queue: Queue) -> str:
    return f"q:{queue.value}"


class RetryJob(Exception):
    """Raise from a handler to retry after `delay` seconds without counting as a failure."""

    def __init__(self, delay: float = 1.0) -> None:
        super().__init__(f"retry in {delay}s")
        self.delay = delay


class PermanentJobError(Exception):
    """Raise from a handler to dead-letter immediately (no retries)."""


Handler = Callable[[Queue, dict[str, Any]], Awaitable[None]]


async def enqueue(
    redis: Redis,
    queue: Queue,
    job: dict[str, Any],
    *,
    job_id: str | None = None,
    dedupe_ttl: int = 24 * 3600,
) -> bool:
    """Add a job. With `job_id`, a second enqueue of the same id is a no-op; returns False."""
    if job_id is not None:
        fresh = await redis.set(f"enq:{job_id}", "1", nx=True, ex=dedupe_ttl)
        if not fresh:
            return False
        job = {**job, "_id": job_id}
    await redis.xadd(stream_key(queue), {"job": json.dumps(job)}, maxlen=100_000, approximate=True)
    return True


async def ensure_groups(redis: Redis, queues: list[Queue]) -> None:
    for q in queues:
        try:
            await redis.xgroup_create(stream_key(q), GROUP, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise


def _backoff(attempt: int) -> float:
    return float(min(2 ** (attempt - 1), 30))


class Worker:
    def __init__(
        self,
        redis: Redis,
        queues: list[Queue],
        handler: Handler,
        *,
        consumer: str,
        concurrency: int = 50,
        max_attempts: int = 5,
        block_ms: int = 1000,  # <= 0 polls instead of blocking
        claim_idle_ms: int = 60_000,
    ) -> None:
        self.redis = redis
        self.queues = queues
        self.handler = handler
        self.consumer = consumer
        self.max_attempts = max_attempts
        self.block_ms = block_ms
        self.claim_idle_ms = claim_idle_ms
        self._slots = asyncio.Semaphore(concurrency)
        self._tasks: set[asyncio.Task[None]] = set()

    # ---- fetching -------------------------------------------------------

    async def _read(self, streams: dict[Any, Any], count: int, block: int | None) -> list[Any]:
        res = await self.redis.xreadgroup(GROUP, self.consumer, streams, count=count, block=block)
        return cast(list[Any], res or [])

    async def fetch(self, count: int, *, block: bool = True) -> list[tuple[Queue, str, str]]:
        """Return up to `count` (queue, msg_id, raw_job), highest-priority queue first."""
        for q in self.queues:  # non-blocking pass in priority order
            res = await self._read({stream_key(q): ">"}, count, None)
            if res:
                return self._flatten(res)
        if not block:
            return []
        if self.block_ms <= 0:  # poll mode (fakeredis blocks the event loop on BLOCK)
            await asyncio.sleep(0.05)
            return []
        res = await self._read({stream_key(q): ">" for q in self.queues}, count, self.block_ms)
        items = self._flatten(res)
        order = {q: i for i, q in enumerate(self.queues)}
        return sorted(items, key=lambda it: order[it[0]])

    def _flatten(self, res: list[Any]) -> list[tuple[Queue, str, str]]:
        out = []
        for stream, entries in res:
            queue = Queue(stream.removeprefix("q:"))
            for msg_id, fields in entries:
                out.append((queue, msg_id, fields["job"]))
        return out

    async def reclaim(self) -> list[tuple[Queue, str, str]]:
        """Take over jobs a crashed consumer left pending for too long."""
        out: list[tuple[Queue, str, str]] = []
        for q in self.queues:
            res = await self.redis.xautoclaim(
                stream_key(q), GROUP, self.consumer, self.claim_idle_ms, "0-0", count=50
            )
            for msg_id, fields in res[1]:
                if fields:  # deleted entries come back as None / empty
                    out.append((q, msg_id, fields["job"]))
        return out

    # ---- processing -----------------------------------------------------

    async def process(self, queue: Queue, msg_id: str, raw: str) -> None:
        job: dict[str, Any] = json.loads(raw)
        attempt = int(job.get("_attempt", 1))
        not_before = float(job.get("_not_before", 0))
        wait = not_before - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            await self.handler(queue, job)
        except RetryJob as r:
            await self._requeue(queue, job, attempt, r.delay, count_attempt=False)
        except PermanentJobError as e:
            await self._dead(queue, job, repr(e))
        except Exception as e:
            log.exception("job failed queue=%s attempt=%d", queue, attempt)
            if attempt >= self.max_attempts:
                await self._dead(queue, job, repr(e))
            else:
                await self._requeue(queue, job, attempt, _backoff(attempt), count_attempt=True)
        await self._ack(queue, msg_id)

    async def _requeue(
        self, queue: Queue, job: dict[str, Any], attempt: int, delay: float, *, count_attempt: bool
    ) -> None:
        job = {**job, "_attempt": attempt + 1 if count_attempt else attempt}
        job["_not_before"] = time.time() + delay
        await self.redis.xadd(stream_key(queue), {"job": json.dumps(job)})

    async def _dead(self, queue: Queue, job: dict[str, Any], error: str) -> None:
        log.error("dead-lettered job queue=%s id=%s", queue, job.get("_id"))
        await self.redis.xadd(
            DEAD_STREAM,
            {"queue": queue.value, "job": json.dumps(job), "error": error[:500]},
            maxlen=10_000,
            approximate=True,
        )

    async def _ack(self, queue: Queue, msg_id: str) -> None:
        key = stream_key(queue)
        await self.redis.xack(key, GROUP, msg_id)
        await self.redis.xdel(key, msg_id)

    # ---- loops ----------------------------------------------------------

    async def run_once(self) -> int:
        """Process everything currently available (no blocking). For tests and tooling."""
        await ensure_groups(self.redis, self.queues)
        n = 0
        while items := await self.fetch(100, block=False):
            for q, msg_id, raw in items:
                await self.process(q, msg_id, raw)
                n += 1
        return n

    async def run(self, stop: asyncio.Event) -> None:
        await ensure_groups(self.redis, self.queues)
        last_reclaim = 0.0
        while not stop.is_set():
            items: list[tuple[Queue, str, str]] = []
            if time.monotonic() - last_reclaim > self.claim_idle_ms / 1000:
                items = await self.reclaim()
                last_reclaim = time.monotonic()
            free = max(1, self._slots._value)
            items += await self.fetch(free)
            for q, msg_id, raw in items:
                await self._slots.acquire()
                task = asyncio.create_task(self._guarded(q, msg_id, raw))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        # drain in-flight jobs before exiting
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _guarded(self, q: Queue, msg_id: str, raw: str) -> None:
        try:
            with contextlib.suppress(asyncio.CancelledError):
                await self.process(q, msg_id, raw)
        finally:
            self._slots.release()
