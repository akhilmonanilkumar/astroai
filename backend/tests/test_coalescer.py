import json
from typing import Any

from guruji.config import Settings
from guruji.ingress.coalescer import DUE_KEY, flush_due, ingest
from guruji.queue.locks import turn_lock_key
from guruji.whatsapp.models import IncomingMessage

WA = "919800000001"


def msg(wamid: str, text: str) -> IncomingMessage:
    return IncomingMessage(wa_id=WA, wamid=wamid, ts=0, kind="text", text=text)


async def turn_jobs(redis: Any) -> list[dict[str, Any]]:
    entries = await redis.xrange("q:turn")
    return [json.loads(fields["job"]) for _, fields in entries]


async def test_burst_merges_into_one_turn(redis: Any, settings: Settings) -> None:
    await ingest(redis, settings, msg("w1", "hi guruji"), now=100.0)
    await ingest(redis, settings, msg("w2", "job change kab hoga?"), now=101.0)

    assert await flush_due(redis, now=102.0) == 0  # still inside the quiet window
    assert await flush_due(redis, now=103.6) == 1

    [job] = await turn_jobs(redis)
    assert job["wa_id"] == WA
    assert [m["text"] for m in job["messages"]] == ["hi guruji", "job change kab hoga?"]
    assert await redis.zcard(DUE_KEY) == 0


async def test_max_wait_caps_a_long_burst(redis: Any, settings: Settings) -> None:
    # A message every 2s would keep extending the quiet window forever; max_seconds caps it.
    for i, t in enumerate([100.0, 102.0, 103.5]):
        await ingest(redis, settings, msg(f"w{i}", str(i)), now=t)
    assert await flush_due(redis, now=104.0) == 1  # first + 4.0 s
    [job] = await turn_jobs(redis)
    assert len(job["messages"]) == 3


async def test_burst_waits_while_turn_is_running(redis: Any, settings: Settings) -> None:
    await ingest(redis, settings, msg("w1", "one"), now=100.0)
    await redis.set(turn_lock_key(WA), "token")

    assert await flush_due(redis, now=110.0) == 0
    await ingest(redis, settings, msg("w2", "two"), now=110.1)  # arrives mid-turn

    await redis.delete(turn_lock_key(WA))
    assert await flush_due(redis, now=120.0) == 1
    [job] = await turn_jobs(redis)
    assert [m["text"] for m in job["messages"]] == ["one", "two"]


async def test_only_first_bubble_triggers_typing(redis: Any, settings: Settings) -> None:
    await ingest(redis, settings, msg("w1", "a"), now=100.0)
    await ingest(redis, settings, msg("w2", "b"), now=100.5)
    assert await redis.xlen("q:send") == 1


async def test_separate_users_do_not_merge(redis: Any, settings: Settings) -> None:
    await ingest(redis, settings, msg("w1", "a"), now=100.0)
    other = IncomingMessage(wa_id="919800000002", wamid="w9", ts=0, kind="text", text="b")
    await ingest(redis, settings, other, now=100.0)
    assert await flush_due(redis, now=200.0) == 2
