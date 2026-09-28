import time
from typing import Any

import pytest

from guruji.queue.streams import (
    DEAD_STREAM,
    PermanentJobError,
    Queue,
    RetryJob,
    Worker,
    enqueue,
)


class Recorder:
    def __init__(self, fail_times: int = 0, exc: type[Exception] = RuntimeError) -> None:
        self.calls: list[tuple[Queue, dict[str, Any]]] = []
        self.fail_times = fail_times
        self.exc = exc

    async def __call__(self, queue: Queue, job: dict[str, Any]) -> None:
        self.calls.append((queue, job))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.exc("boom")


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    # retries are scheduled with _not_before in the future; make "now" jump ahead instead
    real = time.time
    offset = [0.0]

    async def fake_sleep(seconds: float) -> None:
        offset[0] += seconds

    monkeypatch.setattr("guruji.queue.streams.time.time", lambda: real() + offset[0])
    monkeypatch.setattr("guruji.queue.streams.asyncio.sleep", fake_sleep)


async def test_enqueue_with_job_id_is_idempotent(redis: Any) -> None:
    assert await enqueue(redis, Queue.TURN, {"x": 1}, job_id="j1")
    assert not await enqueue(redis, Queue.TURN, {"x": 1}, job_id="j1")
    assert await redis.xlen("q:turn") == 1


async def test_priority_order(redis: Any) -> None:
    rec = Recorder()
    await enqueue(redis, Queue.TURN, {"n": "turn"})
    await enqueue(redis, Queue.CRISIS, {"n": "crisis"})
    worker = Worker(redis, [Queue.CRISIS, Queue.TURN], rec, consumer="t")
    await worker.run_once()
    assert [job["n"] for _, job in rec.calls] == ["crisis", "turn"]


async def test_retry_then_success(redis: Any) -> None:
    rec = Recorder(fail_times=2)
    await enqueue(redis, Queue.TURN, {"n": 1})
    worker = Worker(redis, [Queue.TURN], rec, consumer="t", max_attempts=5)
    await worker.run_once()
    assert [job.get("_attempt", 1) for _, job in rec.calls] == [1, 2, 3]
    assert await redis.xlen(DEAD_STREAM) == 0
    assert await redis.xlen("q:turn") == 0


async def test_dead_letter_after_max_attempts(redis: Any) -> None:
    rec = Recorder(fail_times=99)
    await enqueue(redis, Queue.TURN, {"n": 1})
    worker = Worker(redis, [Queue.TURN], rec, consumer="t", max_attempts=3)
    await worker.run_once()
    assert len(rec.calls) == 3
    assert await redis.xlen(DEAD_STREAM) == 1


async def test_permanent_error_skips_retries(redis: Any) -> None:
    rec = Recorder(fail_times=1, exc=PermanentJobError)
    await enqueue(redis, Queue.TURN, {"n": 1})
    await Worker(redis, [Queue.TURN], rec, consumer="t").run_once()
    assert len(rec.calls) == 1
    assert await redis.xlen(DEAD_STREAM) == 1


async def test_retry_job_does_not_count_attempts(redis: Any) -> None:
    rec = Recorder(fail_times=10, exc=lambda _msg: RetryJob(0.1))  # type: ignore[arg-type]
    await enqueue(redis, Queue.TURN, {"n": 1})
    await Worker(redis, [Queue.TURN], rec, consumer="t", max_attempts=2).run_once()
    assert len(rec.calls) == 11
    assert await redis.xlen(DEAD_STREAM) == 0


def test_loadtest_report() -> None:
    from guruji.loadtest import Result, percentile, report

    assert percentile([], 50) == 0.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) in (2.0, 3.0)
    r = Result(latencies=[0.5, 1.0, 4.0], timeouts=1, started=0.0, finished=2.0)
    text = report(r)
    assert "sent: 4" in text and "timeouts: 1" in text and "p50" in text
