"""PR-09: personal data in Redis is forgotten on erasure and trimmed by retention."""

import json
import time
from typing import Any

from guruji.queue.forget import forget_user, trim_dead_letters
from guruji.queue.streams import DEAD_STREAM


async def _dead(redis: Any, job: dict[str, Any], at_ms: int | None = None) -> None:
    fields = {"queue": "turn", "job": json.dumps(job), "error": "x"}
    await redis.xadd(DEAD_STREAM, fields, id=f"{at_ms}-0" if at_ms else "*")


async def test_forget_user_drops_their_dead_letters_only(redis: Any) -> None:
    await _dead(redis, {"kind": "turn", "wa_id": "919800000001", "messages": ["secret"]})
    await _dead(redis, {"kind": "export", "user_id": "u-1"})
    await _dead(redis, {"kind": "turn", "wa_id": "919800000002"})
    await redis.set("enq:capi:lead:919800000001", "1")
    assert await forget_user(redis, "919800000001", "u-1") == 2
    left = [json.loads(f["job"]) for _, f in await redis.xrange(DEAD_STREAM)]
    assert left == [{"kind": "turn", "wa_id": "919800000002"}]
    assert not await redis.exists("enq:capi:lead:919800000001")


async def test_forget_user_pages_through_many_dead_letters(redis: Any) -> None:
    for i in range(1200):
        await _dead(redis, {"wa_id": "919800000001" if i % 2 else "919800000003", "i": i})
    assert await forget_user(redis, "919800000001") == 600
    assert await redis.xlen(DEAD_STREAM) == 600


async def test_old_dead_letters_are_trimmed(redis: Any) -> None:
    now_ms = int(time.time() * 1000)
    await _dead(redis, {"n": "old"}, at_ms=now_ms - 20 * 86_400_000)
    await _dead(redis, {"n": "new"}, at_ms=now_ms - 1000)
    assert await trim_dead_letters(redis, 14) == 1
    [(_, fields)] = await redis.xrange(DEAD_STREAM)
    assert json.loads(fields["job"]) == {"n": "new"}


async def test_turn_worker_forgets_an_erased_user(redis: Any, settings: Any) -> None:
    from guruji.queue.streams import Queue
    from guruji.turn.reply import Reply
    from guruji.turn.worker import Turn, TurnHandler

    class Erasing:
        async def respond(self, turn: Turn) -> Reply:
            return Reply(["Everything is erased."], kind="privacy", erased=True)

    await _dead(redis, {"kind": "turn", "wa_id": "919800000001"})
    msg = {"wa_id": "919800000001", "wamid": "wamid.e1", "ts": 1, "kind": "reply"}
    job = {"kind": "turn", "wa_id": "919800000001", "messages": [msg]}
    await TurnHandler(redis, settings, Erasing())(Queue.TURN, job)
    assert await redis.xlen(DEAD_STREAM) == 0
    assert await redis.xlen("q:send") == 1  # the confirmation still goes out
