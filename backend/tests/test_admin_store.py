"""AdminStore contract, on MemoryStore and (with TEST_DATABASE_URL) Postgres."""

import uuid
from datetime import UTC, datetime, timedelta

from guruji.appconfig import DEFAULTS
from guruji.db.models import Consent, EncryptedBirth, EscalationOpen, InboundLog, TurnWrite
from guruji.db.store import Store


def _turn(user_id: str, turn_id: str, text: str | None = "hello", **kw: object) -> TurnWrite:
    return TurnWrite(
        user_id=user_id,
        turn_id=turn_id,
        inbound=[InboundLog(f"wamid.{turn_id}", "text", text)],
        **kw,  # type: ignore[arg-type]
    )


async def _escalated_user(store: Store, wa_hash: str = "h1") -> tuple[str, str]:
    user, _ = await store.get_or_create_user(wa_hash)
    esc = EscalationOpen(str(uuid.uuid4()), "human_requested", 3, "active")
    await store.commit_turn(
        _turn(user.id, f"t-{wa_hash}", reply_body="ok", state="escalated", escalation=esc)
    )
    return user.id, esc.id


async def test_admins(store: Store) -> None:
    assert await store.get_admin("a@x.in") is None
    a = await store.add_admin("A@X.in", "agent")
    assert (a.email, a.role, a.disabled) == ("a@x.in", "agent", False)
    b = await store.add_admin("a@x.in", "owner")
    assert b.id == a.id and b.role == "owner"
    assert await store.get_admin("A@x.IN") == b


async def test_users_list_and_record(store: Store) -> None:
    first, _ = await store.get_or_create_user("h-first")
    second, _ = await store.get_or_create_user("h-second")
    await store.commit_turn(
        _turn(
            second.id,
            "t1",
            state="active",
            language="hi",
            consents=[Consent("v1", "readings", True, True, "wamid.c", datetime.now(UTC))],
            birth=EncryptedBirth(b"n", b"d", None, False, b"p", b"la", b"lo", "Asia/Kolkata"),
            chart=("astro-1", {"a": 1}),
        )
    )
    rows = await store.list_users()
    assert [r.id for r in rows] == [second.id, first.id]  # newest first
    assert rows[0].onboarded and rows[0].last_inbound_at is not None
    assert not rows[1].onboarded and rows[1].last_inbound_at is None
    assert [r.id for r in await store.list_users(state="active")] == [second.id]
    assert [r.id for r in await store.list_users(wa_hash="h-first")] == [first.id]
    assert await store.list_users(wa_hash="nobody") == []
    assert [r.id for r in await store.list_users(before=rows[0].created_at)] == [first.id]

    record = await store.user_record(second.id)
    assert record is not None
    assert record.row.language == "hi" and record.birth is not None
    assert [c.wamid for c in record.consents] == ["wamid.c"]
    assert await store.user_record(str(uuid.uuid4())) is None


async def test_messages_page(store: Store) -> None:
    user, _ = await store.get_or_create_user("h")
    for i in range(3):
        await store.commit_turn(_turn(user.id, f"t{i}", text=f"q{i}", reply_body=f"a{i}"))
    page = await store.messages_page(user.id, limit=4)
    assert [m.body for m in page] == ["q1", "a1", "q2", "a2"]
    older = await store.messages_page(user.id, before_id=page[0].id, limit=10)
    assert [m.body for m in older] == ["q0", "a0"]


async def test_escalation_inbox_reply_and_close(store: Store) -> None:
    admin = await store.add_admin("team@x.in", "agent")
    user_id, esc_id = await _escalated_user(store)
    [row] = await store.list_escalations(active=True)
    assert row.escalation.id == esc_id and row.user_state == "escalated"
    assert row.last_inbound_at is not None
    assert await store.list_escalations(active=False) == []

    meta = {"kind": "human", "bubbles": ["hi"], "buttons": []}
    kw = {"kind": "text", "body": "hi", "meta": meta, "admin_id": admin.id}
    assert await store.log_human_message(user_id, "h_1", **kw)  # type: ignore[arg-type]
    assert not await store.log_human_message(user_id, "h_1", **kw)  # type: ignore[arg-type]
    last = (await store.messages_page(user_id))[-1]
    assert (last.direction, last.sent_by, last.body) == ("out", "human", "hi")
    recent = await store.recent_messages(user_id, 5)
    assert recent[-1].meta == meta  # the turn graph sees the team's message

    assert await store.close_escalation(esc_id, hand_back=True, by="admin:1", note="talked")
    assert not await store.close_escalation(esc_id, hand_back=True, by="admin:1", note=None)
    [closed] = await store.list_escalations(active=False)
    assert closed.escalation.status == "handed_back" and closed.notes == "talked"
    assert closed.resolved_at is not None and closed.user_state == "active"


async def test_active_escalations_most_urgent_first(store: Store) -> None:
    _, low = await _escalated_user(store, "h-low")
    user, _ = await store.get_or_create_user("h-crisis")
    urgent = EscalationOpen(str(uuid.uuid4()), "crisis", 1, "active")
    await store.commit_turn(_turn(user.id, "t-c", state="escalated", escalation=urgent))
    assert [r.escalation.id for r in await store.list_escalations(active=True)] == [
        urgent.id,
        low,
    ]
    row = await store.escalation_row(urgent.id)
    assert row is not None and row.escalation.category == "crisis"


async def test_block_and_unblock(store: Store) -> None:
    user, _ = await store.get_or_create_user("h")
    assert await store.set_blocked(user.id, False) is None  # not blocked
    assert await store.set_blocked(user.id, True) == "blocked"
    assert await store.set_blocked(user.id, True) is None
    assert await store.set_blocked(user.id, False) == "new"  # not onboarded: start over
    await store.commit_turn(_turn(user.id, "t", state="active", chart=("astro-1", {})))
    await store.set_blocked(user.id, True)
    assert await store.set_blocked(user.id, False) == "active"
    escalated, _ = await _escalated_user(store, "h2")
    assert await store.set_blocked(escalated, True) is None  # close the case first


async def test_credits_are_idempotent(store: Store) -> None:
    user, _ = await store.get_or_create_user("h")
    assert await store.add_credits(user.id, 5, "adjust", "k1", {"note": "goodwill"})
    assert not await store.add_credits(user.id, 5, "adjust", "k1")
    assert await store.add_credits(user.id, -2, "adjust", "k2")
    record = await store.user_record(user.id)
    assert record is not None and record.row.balance == 3
    assert [e.delta for e in record.ledger] == [-2, 5]  # newest first


async def test_config_and_audit(store: Store) -> None:
    assert await store.get_config("flags") == DEFAULTS["flags"]
    assert await store.get_config("nope") is None
    new = {**DEFAULTS["flags"], "voice_enabled": False}
    await store.set_config("flags", new, "admin:1")
    assert await store.get_config("flags") == new
    entries = {e.key: e for e in await store.config_entries()}
    assert entries["flags"].updated_by == "admin:1"
    assert set(DEFAULTS) <= set(entries)  # the migration seeds every known key

    user, _ = await store.get_or_create_user("h")
    await store.audit("admin:1", "view_user", user.id)
    await store.audit("admin:1", "config_update", None, {"key": "flags"})
    assert [a.action for a in await store.audit_entries()] == ["config_update", "view_user"]
    [mine] = await store.audit_entries(subject_user_id=user.id)
    assert (mine.actor, mine.subject_user_id) == ("admin:1", user.id)


async def test_metrics(store: Store) -> None:
    await _escalated_user(store)
    other, _ = await store.get_or_create_user("h2")
    await store.commit_turn(
        _turn(
            other.id,
            "t-ad",
            state="active",
            chart=("astro-1", {}),
            consents=[Consent("v1", "readings", True, True, "wamid.c", datetime.now(UTC))],
            referral={"source_type": "ad", "source_id": "ad-7", "raw": 1},
        )
    )
    await store.add_credits(other.id, 10, "purchase", "pay-1")
    now = datetime.now(UTC)
    m = await store.metrics(now, 7)
    assert m.funnel == {"started": 2, "consented": 1, "onboarded": 1, "paid": 1}
    assert m.users_by_state == {"escalated": 1, "active": 1}
    assert len(m.days) == 7 and m.days[-1].new_users == 2
    assert m.days[-1].messages_in == 2 and m.days[-1].escalations == 1
    assert m.open_escalations == {"human_requested": 1}
    assert [(a.source_id, a.users, a.onboarded, a.paid) for a in m.ads] == [("ad-7", 1, 1, 1)]
    assert m.median_ack_minutes is None
    later = await store.metrics(now + timedelta(days=30), 7)
    assert sum(d.new_users for d in later.days) == 0


async def test_missing_tables_are_reported() -> None:
    import os

    import pytest

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run the Postgres store tests")
    from guruji.db.postgres import PostgresStore

    store = await PostgresStore.connect(url, max_size=1)
    try:
        assert await store.missing_tables(["users", "no_such_table"]) == ["no_such_table"]
    finally:
        await store.aclose()


async def test_turn_credits_meter_and_passes(store: Store) -> None:
    from guruji.db.models import CreditWrite

    user, _ = await store.get_or_create_user("h-credits")
    await store.add_credits(user.id, 5, "purchase", "pay-9")
    w = _turn(
        user.id,
        "t-spend",
        reply_body="answer",
        meter={"welcome_used": 1},
        credits=[CreditWrite(-2, "spend", "spend:t-spend", {"prashna": "t-spend"})],
    )
    assert await store.commit_turn(w)
    assert not await store.commit_turn(w)  # redelivered: charged once
    assert await store.balance(user.id) == 3
    assert await store.credit_by_key("spend:t-spend") == (user.id, -2)
    assert await store.credit_by_key("refund:t-spend") is None
    again, _ = await store.get_or_create_user("h-credits")
    assert again.meter == {"welcome_used": 1}

    now = datetime.now(UTC)
    assert await store.active_pass(user.id, now) is None
    first = await store.add_pass(user.id, "plus_monthly", 30, "payment:p1")
    assert first is not None
    assert await store.add_pass(user.id, "plus_monthly", 30, "payment:p1") is None
    second = await store.add_pass(user.id, "plus_monthly", 30, "payment:p2")
    assert second is not None and abs((second.starts_at - first.ends_at).total_seconds()) < 5
    live = await store.active_pass(user.id, now + timedelta(days=1))
    assert live is not None and live.plan_id == "plus_monthly"
    assert await store.active_pass(user.id, now + timedelta(days=61)) is None


async def test_orders_are_fulfilled_once(store: Store) -> None:
    from guruji.db.models import Order

    user, _ = await store.get_or_create_user("h-orders")
    await store.create_order(Order("gj-a", user.id, "pack", "p51", 5100, prashnas=10))
    await store.create_order(Order("gj-a", user.id, "pack", "p51", 5100, prashnas=10))
    assert not await store.has_paid(user.id)
    paid = await store.fulfil_order("gj-a", "pay_1")
    assert paid is not None and paid.status == "paid"
    assert await store.fulfil_order("gj-a", "pay_1") is None
    assert await store.balance(user.id) == 10 and await store.has_paid(user.id)

    await store.create_order(Order("gj-b", user.id, "pass", "plus_monthly", 19900, days=30))
    assert await store.fulfil_order("gj-b", "pay_2") is not None
    assert await store.active_pass(user.id, datetime.now(UTC)) is not None

    await store.create_order(Order("gj-c", user.id, "pack", "p51", 5100, prashnas=10))
    assert await store.fail_order("gj-c")
    assert await store.fulfil_order("gj-c", "pay_3") is None
    got = await store.get_order("gj-c")
    assert got is not None and got.status == "failed"


async def test_erase_keeps_payments_but_nothing_personal(store: Store) -> None:
    from guruji.db.models import Order

    user_id, _ = await _escalated_user(store, "h-erase")
    await store.set_wa_id(user_id, b"\x01enc")
    await store.commit_turn(
        _turn(
            user_id,
            "t-e2",
            birth=EncryptedBirth(b"n", b"d", None, False, b"p", b"la", b"lo", "Asia/Kolkata"),
            chart=("astro-1", {}),
            facts=[("career", "nurse")],
        )
    )
    await store.create_order(Order("gj-e", user_id, "pack", "p51", 5100, prashnas=10))
    await store.fulfil_order("gj-e", "pay_e")
    assert await store.erase_user(user_id)
    assert not await store.erase_user(user_id)
    assert await store.get_birth(user_id) is None and await store.wa_id_enc(user_id) is None
    assert await store.messages_page(user_id) == [] and await store.facts(user_id, 10) == []
    assert await store.get_chart(user_id, "astro-1") is None
    assert await store.balance(user_id) == 10  # the ledger stays (tax law)
    assert await store.list_users(wa_hash="h-erase") == []
    fresh, created = await store.get_or_create_user("h-erase")
    assert created and fresh.id != user_id  # writing again starts from scratch


async def test_retention_sweep(store: Store) -> None:
    from guruji.db.models import Order

    stopped, _ = await store.get_or_create_user("h-stopped")
    await store.commit_turn(_turn(stopped.id, "t-stop", state="opted_out", opted_out=True))
    kept, _ = await store.get_or_create_user("h-kept")
    await store.commit_turn(_turn(kept.id, "t-kept"))
    await store.create_order(Order("gj-old", kept.id, "pack", "p51", 5100, prashnas=10))
    now = datetime.now(UTC)
    soon = await store.retention_sweep(
        now, opted_out_days=180, message_days=730, pending_order_hours=48
    )
    assert soon == {"users_erased": 0, "messages_dropped": 0, "orders_expired": 0}
    later = await store.retention_sweep(
        now + timedelta(days=800), opted_out_days=180, message_days=730, pending_order_hours=48
    )
    assert later["users_erased"] == 1 and later["messages_dropped"] >= 1
    assert later["orders_expired"] == 1
    assert await store.list_users(wa_hash="h-stopped") == []
    order = await store.get_order("gj-old")
    assert order is not None and order.status == "expired"
