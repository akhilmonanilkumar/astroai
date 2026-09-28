"""Who pays for a question."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from guruji.agent.metering import Meter, Rules, decide
from guruji.appconfig import SCHEMAS

RULES = Rules(
    SCHEMAS["free_tier"].validate_python(
        {"welcome_hours": 72, "welcome_prashnas": 2, "daily_free_answers": 1}
    ),
    SCHEMAS["prashna"].validate_python(
        {"followups": 2, "voice_credit_cost": 2, "followup_hours": 12}
    ),
    SCHEMAS["plus_limits"].validate_python({"prashnas_per_day": 2}),
)
JOINED = datetime(2026, 9, 1, 6, tzinfo=UTC)
LATER = datetime(2026, 9, 11, 1, tzinfo=UTC)  # 06:30 IST: +13 h is still the same day


def ask(meter: Meter, now: datetime, **kw: object) -> tuple[str, int, Meter]:
    args: dict[str, object] = {"balance": 0, "plus_active": False, "voice": False}
    args.update(kw)
    d = decide(meter, RULES, now=now, joined=JOINED, turn_id=f"t{now:%H%M%S}", **args)  # type: ignore[arg-type]
    return d.kind, d.cost, d.meter


def test_welcome_then_followups_then_daily_then_empty() -> None:
    m, now = Meter(), JOINED + timedelta(hours=1)
    kind, _, m = ask(m, now)
    assert kind == "welcome" and m.followups_left == 2
    for _ in range(2):
        now += timedelta(minutes=5)
        kind, _, m = ask(m, now)
        assert kind == "followup"
    now += timedelta(minutes=5)
    kind, _, m = ask(m, now)
    assert kind == "welcome" and m.welcome_used == 2  # followups used up: a new prashna
    now += timedelta(hours=13)
    kind, _, m = ask(m, now)
    assert kind == "empty"  # welcome allowance gone, still inside the window: no daily
    kind, _, m = ask(m, LATER)
    assert kind == "daily"
    kind, _, m = ask(m, LATER + timedelta(hours=13))
    assert kind == "empty"
    kind, _, m = ask(m, LATER + timedelta(days=1))
    assert kind == "daily"  # a new IST day


def test_credits_ask_once_a_day_then_charge() -> None:
    m = replace(Meter(), day_free_used=1, day="2026-09-11")
    kind, cost, m2 = ask(m, LATER, balance=5)
    assert (kind, cost) == ("confirm", 1) and m2 == m.on(LATER)
    kind, cost, m = ask(m, LATER, balance=5, accepted=True)
    assert (kind, cost, m.paid_by) == ("charge", 1, "credit")
    kind, _, m = ask(m, LATER + timedelta(hours=13), balance=4)
    assert kind == "charge"  # already agreed today


def test_voice_costs_more_and_falls_back_to_text() -> None:
    m = replace(Meter(), day_free_used=1, day="2026-09-11", spend_ok_day="2026-09-11")
    d = decide(
        m, RULES, now=LATER, joined=JOINED, turn_id="t", balance=5, plus_active=False, voice=True
    )
    assert (d.kind, d.cost, d.voice) == ("charge", 2, True)
    d = decide(
        m, RULES, now=LATER, joined=JOINED, turn_id="t", balance=1, plus_active=False, voice=True
    )
    assert (d.kind, d.cost, d.voice) == ("charge", 1, False)


def test_plus_pass_before_daily_and_credits() -> None:
    kind, _, m = ask(Meter(), LATER, plus_active=True, balance=3)
    assert kind == "plus"
    kind, _, m = ask(m, LATER + timedelta(hours=13), plus_active=True, balance=3)
    assert kind == "plus" and m.day_plus_used == 2
    m = replace(m, followups_left=0)
    kind, _, m = ask(m, LATER + timedelta(hours=14), plus_active=True, balance=3)
    assert kind == "daily"  # the day's Plus limit reached


def test_meter_round_trips() -> None:
    m = replace(
        Meter(),
        prashna_id="t1",
        prashna_opened=LATER,
        followups_left=1,
        paid_by="credit",
        pending={"text": "q", "voice": False},
    )
    assert Meter.load(m.dump()) == m
    assert Meter.load(None) == Meter()
