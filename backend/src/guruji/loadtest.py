"""Load test through the whole pipeline, using the simulator as the phones and as Meta.

    python -m guruji dev                       # or docker compose up (any roles, any scale)
    python -m guruji loadtest --users 200 --messages 3 --concurrency 50

Each synthetic user sends messages through the simulator (signed webhooks to ingress,
exactly as Meta would) and waits for Guruji's first bubble, which comes back through the
sender to the simulator's fake Graph API. Reports time to first bubble (p50/p90/p99),
timeouts and throughput. New users are onboarding, so this measures the pipeline
(ingress, burst merging, queues, locks, store, sender) more than the LLM; point the
model settings at a real provider to include it.

Targets from the plan: typing within 1 s, first bubble in 3-8 s for text.
"""

import asyncio
import statistics
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class Result:
    latencies: list[float] = field(default_factory=list)
    timeouts: int = 0
    errors: int = 0
    started: float = field(default_factory=time.monotonic)
    finished: float = 0.0

    @property
    def sent(self) -> int:
        return len(self.latencies) + self.timeouts + self.errors


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


def report(r: Result) -> str:
    lat = r.latencies
    secs = max(r.finished - r.started, 1e-9)
    lines = [
        f"messages sent: {r.sent}  answered: {len(lat)}  timeouts: {r.timeouts}  "
        f"errors: {r.errors}",
        f"throughput: {len(lat) / secs:.1f} answered/s over {secs:.1f}s",
    ]
    if lat:
        lines.append(
            "first bubble: "
            f"p50 {percentile(lat, 50):.2f}s  p90 {percentile(lat, 90):.2f}s  "
            f"p99 {percentile(lat, 99):.2f}s  max {max(lat):.2f}s  "
            f"mean {statistics.mean(lat):.2f}s"
        )
    return "\n".join(lines)


async def _one_message(
    http: httpx.AsyncClient, wa_id: str, text: str, wait_s: float, r: Result
) -> None:
    seen = len((await http.get("/api/history", params={"wa_id": wa_id})).json())
    t0 = time.monotonic()
    resp = await http.post("/api/send", json={"wa_id": wa_id, "name": "Load", "text": text})
    if resp.status_code != 200 or resp.json().get("ingress_status") != 200:
        r.errors += 1
        return
    while time.monotonic() - t0 < wait_s:
        events = (await http.get("/api/history", params={"wa_id": wa_id})).json()
        if any(e.get("type") == "out" for e in events[seen:]):
            r.latencies.append(time.monotonic() - t0)
            return
        await asyncio.sleep(0.1)
    r.timeouts += 1


async def run(
    simulator: str,
    *,
    users: int,
    messages: int,
    concurrency: int,
    wait_s: float = 30.0,
    think: float = 1.0,
    texts: tuple[str, ...] = ("namaste", "hi guruji", "hello"),
) -> Result:
    result = Result()
    slots = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(base_url=simulator.rstrip("/"), timeout=15.0) as http:

        async def user(i: int) -> None:
            wa_id = f"9170{int(time.time()) % 1_000_000:06d}{i:04d}"
            async with slots:
                for n in range(messages):
                    try:
                        await _one_message(http, wa_id, texts[n % len(texts)], wait_s, result)
                    except httpx.HTTPError:
                        result.errors += 1
                    await asyncio.sleep(think)  # a real person pauses between messages

        await asyncio.gather(*(user(i) for i in range(users)))
    result.finished = time.monotonic()
    return result
