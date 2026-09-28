"""Store contract, run against MemoryStore and (with TEST_DATABASE_URL) real Postgres."""

from datetime import UTC, datetime

from guruji.db.models import Consent, EncryptedBirth, InboundLog, TurnWrite
from guruji.db.store import Store


def _write(user_id: str, turn_id: str, **kw: object) -> TurnWrite:
    return TurnWrite(
        user_id=user_id,
        turn_id=turn_id,
        inbound=[InboundLog(f"wamid.{turn_id}", "text", "hello")],
        **kw,  # type: ignore[arg-type]
    )


async def test_users_are_found_by_hash(store: Store) -> None:
    a, created = await store.get_or_create_user("hash-a")
    again, created_again = await store.get_or_create_user("hash-a")
    b, _ = await store.get_or_create_user("hash-b")
    assert created and not created_again
    assert a.id == again.id != b.id
    assert a.state == "new" and a.onboarding == {}
    await store.set_wa_id(a.id, b"\x01enc")


async def test_invite_admission_is_kept(store: Store) -> None:
    user, _ = await store.get_or_create_user("h-invite")
    assert not user.admitted
    await store.commit_turn(_write(user.id, "t-a", admitted_by="GURU-BETA"))
    again, _ = await store.get_or_create_user("h-invite")
    assert again.admitted
    await store.commit_turn(_write(user.id, "t-b", admitted_by="OTHER-CODE"))  # first one kept
    assert (await store.get_or_create_user("h-invite"))[0].admitted


async def test_commit_turn_is_idempotent(store: Store) -> None:
    user, _ = await store.get_or_create_user("h")
    w = _write(
        user.id,
        "t1",
        reply_body="namaste",
        reply_meta={"bubbles": ["namaste"], "buttons": []},
        state="consented",
        language="hinglish",
        onboarding={"enc": "x"},
        consents=[
            Consent("v1", "readings", True, False, "wamid.btn", datetime(2026, 9, 28, tzinfo=UTC))
        ],
        facts=[("career", "Works as a nurse in Pune")],
        readings=[("career", "Change favoured from March", ["Jupiter 10th"])],
    )
    assert await store.commit_turn(w) is True
    assert await store.commit_turn(w) is False  # redelivery: nothing applied twice
    assert len(await store.facts(user.id, 10)) == 1
    readings = await store.readings(user.id, 10)
    assert [(r.topic, r.factors) for r in readings] == [("career", ["Jupiter 10th"])]
    stored = await store.stored_reply("t1")
    assert stored is not None and stored.body == "namaste"
    assert stored.meta == {"bubbles": ["namaste"], "buttons": []}
    msgs = await store.recent_messages(user.id, 10)
    assert [(m.direction, m.body) for m in msgs] == [("in", "hello"), ("out", "namaste")]
    again, _ = await store.get_or_create_user("h")
    assert (again.state, again.language, again.onboarding) == (
        "consented",
        "hinglish",
        {"enc": "x"},
    )


async def test_turn_without_reply_still_marks_done(store: Store) -> None:
    user, _ = await store.get_or_create_user("h")
    assert await store.commit_turn(_write(user.id, "t-silent")) is True
    assert await store.commit_turn(_write(user.id, "t-silent")) is False
    stored = await store.stored_reply("t-silent")
    assert stored is not None and stored.body == ""


async def test_birth_and_chart(store: Store) -> None:
    user, _ = await store.get_or_create_user("h")
    birth = EncryptedBirth(b"n", b"d", None, False, b"p", b"la", b"lo", "Asia/Kolkata")
    await store.commit_turn(_write(user.id, "t2", birth=birth, chart=("astro-1", {"a": 1})))
    assert await store.get_birth(user.id) == birth
    assert await store.get_chart(user.id, "astro-1") == {"a": 1}
    assert await store.get_chart(user.id, "astro-2") is None


async def test_referral_logged(store: Store) -> None:
    user, _ = await store.get_or_create_user("h")
    ref = {"source_type": "ad", "source_id": "123", "ctwa_clid": "c", "headline": "Ask Guruji"}
    assert await store.commit_turn(_write(user.id, "t3", referral=ref))


async def test_escalation_lifecycle(store: Store) -> None:
    from datetime import UTC, datetime, timedelta

    from guruji.db.models import EscalationOpen

    user, _ = await store.get_or_create_user("h")
    first = EscalationOpen(
        "11111111-1111-1111-1111-111111111111", "human_requested", 3, "onboarding"
    )
    await store.commit_turn(_write(user.id, "e1", state="escalated", escalation=first))
    # a second signal while one is active does not stack a new case
    second = EscalationOpen("22222222-2222-2222-2222-222222222222", "crisis", 1, "escalated")
    await store.commit_turn(_write(user.id, "e2", escalation=second))
    active = await store.active_escalation(user.id)
    assert active is not None and active.id == first.id and active.status == "open"
    assert await store.get_escalation(second.id) is None

    await store.mark_alerted(first.id)
    got = await store.get_escalation(first.id)
    assert got is not None and got.alert_count == 1 and got.last_alerted_at is not None
    future = datetime.now(UTC) + timedelta(minutes=1)
    assert await store.escalations_to_reping(future) == []  # severity 3 is never re-pinged

    assert await store.acknowledge_escalation(first.id, "telegram:1")
    assert not await store.acknowledge_escalation(first.id, "telegram:1")
    assert await store.resolve_escalation(first.id, hand_back=True)
    assert not await store.resolve_escalation(first.id, hand_back=True)
    again, _ = await store.get_or_create_user("h")
    assert again.state == "onboarding"  # back to where they were
    assert await store.active_escalation(user.id) is None


async def test_urgent_escalations_are_repinged(store: Store) -> None:
    from datetime import UTC, datetime, timedelta

    from guruji.db.models import EscalationOpen

    user, _ = await store.get_or_create_user("h")
    esc = EscalationOpen("33333333-3333-3333-3333-333333333333", "crisis", 1, "active")
    await store.commit_turn(_write(user.id, "e3", state="escalated", escalation=esc))
    assert await store.escalations_to_reping(datetime.now(UTC)) == []  # never alerted yet
    await store.mark_alerted(esc.id)
    later = datetime.now(UTC) + timedelta(minutes=10)
    assert [e.id for e in await store.escalations_to_reping(later)] == [esc.id]
    await store.acknowledge_escalation(esc.id, "telegram:9")
    assert await store.escalations_to_reping(later) == []
