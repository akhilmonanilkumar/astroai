"""Postgres `Store` (Supabase, via the Supavisor pooler in transaction mode).

Transaction mode cannot keep prepared statements across checkouts, so they are off.
"""

from datetime import date, datetime, timedelta
from typing import Any

from psycopg import AsyncConnection, Rollback
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from guruji.db.admin import (
    AdminMessage,
    AdminRole,
    AdminUser,
    AdStats,
    AuditEntry,
    ConfigEntry,
    CreditReason,
    DayStats,
    EscalationRow,
    FeedbackRow,
    LedgerEntry,
    Metrics,
    UserRecord,
    UserRow,
)
from guruji.db.models import (
    Consent,
    EncryptedBirth,
    Escalation,
    LifeFact,
    LoggedMessage,
    Order,
    Pass,
    Rating,
    Reading,
    StoredReply,
    TurnWrite,
    User,
    UserState,
)

_USER_COLS = (
    "id::text, state::text, language, onboarding, created_at, meter, "
    "admitted_at is not null as admitted"
)


def _user(row: dict[str, Any]) -> User:
    return User(
        id=row["id"],
        state=row["state"],
        language=row["language"],
        onboarding=row["onboarding"] or {},
        created_at=row["created_at"],
        meter=row["meter"] or {},
        admitted=bool(row["admitted"]),
    )


class PostgresStore:
    def __init__(self, pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]]) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, url: str, max_size: int = 5) -> "PostgresStore":
        pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]] = AsyncConnectionPool(
            url,
            min_size=1,
            max_size=max_size,
            open=False,
            kwargs={"prepare_threshold": None, "row_factory": dict_row},
        )
        await pool.open(wait=True)
        return cls(pool)

    async def missing_tables(self, names: list[str]) -> list[str]:
        """Which of these tables don't exist yet (a migration was not applied)."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select n from unnest(%s::text[]) as n where to_regclass('public.' || n) is null",
                (names,),
            )
            return [r["n"] for r in await cur.fetchall()]

    @property
    def pool(self) -> AsyncConnectionPool[AsyncConnection[dict[str, Any]]]:
        return self._pool

    async def aclose(self) -> None:
        await self._pool.close()

    async def get_or_create_user(self, wa_hash: str) -> tuple[User, bool]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"insert into users (wa_id_hash) values (%s) on conflict (wa_id_hash) do nothing "
                f"returning {_USER_COLS}",
                (wa_hash,),
            )
            row = await cur.fetchone()
            if row is not None:
                return _user(row), True
            cur = await conn.execute(
                f"select {_USER_COLS} from users where wa_id_hash = %s", (wa_hash,)
            )
            row = await cur.fetchone()
            assert row is not None
            return _user(row), False

    async def set_wa_id(self, user_id: str, wa_id_enc: bytes) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "update users set wa_id_enc = %s where id = %s", (wa_id_enc, user_id)
            )

    async def get_birth(self, user_id: str) -> EncryptedBirth | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select name_enc, birth_date_enc, birth_time_enc, time_known, place_label_enc, "
                "latitude_enc, longitude_enc, tz_name from birth_details where user_id = %s",
                (user_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return EncryptedBirth(
            name_enc=row["name_enc"],
            date_enc=row["birth_date_enc"],
            time_enc=row["birth_time_enc"],
            time_known=row["time_known"],
            place_enc=row["place_label_enc"],
            latitude_enc=row["latitude_enc"],
            longitude_enc=row["longitude_enc"],
            tz_name=row["tz_name"],
        )

    async def get_chart(self, user_id: str, engine_version: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select dossier from charts where user_id = %s and engine_version = %s",
                (user_id, engine_version),
            )
            row = await cur.fetchone()
        return None if row is None else dict(row["dossier"])

    async def recent_messages(self, user_id: str, limit: int) -> list[LoggedMessage]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select direction, sent_by, kind, body, created_at, meta from messages "
                "where user_id = %s and body is not null order by id desc limit %s",
                (user_id, limit),
            )
            rows = await cur.fetchall()
        return [
            LoggedMessage(
                r["direction"], r["sent_by"], r["kind"], r["body"], r["created_at"], r["meta"]
            )
            for r in reversed(rows)
        ]

    async def facts(self, user_id: str, limit: int) -> list[LifeFact]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select category, fact, created_at from life_facts where user_id = %s "
                "order by id desc limit %s",
                (user_id, limit),
            )
            rows = await cur.fetchall()
        return [LifeFact(r["category"], r["fact"], r["created_at"]) for r in reversed(rows)]

    async def readings(self, user_id: str, limit: int) -> list[Reading]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select topic, summary, factors, created_at from readings where user_id = %s "
                "order by id desc limit %s",
                (user_id, limit),
            )
            rows = await cur.fetchall()
        return [
            Reading(r["topic"], r["summary"], list(r["factors"]), r["created_at"])
            for r in reversed(rows)
        ]

    async def stored_reply(self, turn_id: str) -> StoredReply | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select body, meta from messages where turn_id = %s and direction = 'out'",
                (turn_id,),
            )
            row = await cur.fetchone()
        return None if row is None else StoredReply(row["body"] or "", row["meta"])

    async def balance(self, user_id: str) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select coalesce(sum(delta), 0)::int as b from credit_ledger where user_id = %s",
                (user_id,),
            )
            row = await cur.fetchone()
        return int(row["b"]) if row else 0

    async def active_pass(self, user_id: str, now: datetime) -> Pass | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select plan_id, starts_at, ends_at from passes where user_id = %s "
                "and starts_at <= %s and ends_at > %s order by ends_at desc limit 1",
                (user_id, now, now),
            )
            row = await cur.fetchone()
        return None if row is None else Pass(row["plan_id"], row["starts_at"], row["ends_at"])

    async def credit_by_key(self, key: str) -> tuple[str, int] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select user_id::text, delta from credit_ledger where idempotency_key = %s",
                (key,),
            )
            row = await cur.fetchone()
        return None if row is None else (row["user_id"], int(row["delta"]))

    _ORDER_COLS = (
        "reference_id, user_id::text, kind, item_id, amount_paise, prashnas, days, status, "
        "payment_id, created_at"
    )

    async def erase_user(self, user_id: str) -> bool:
        async with self._pool.connection() as conn, conn.transaction():
            return await self._erase(conn, user_id)

    @staticmethod
    async def _erase(conn: AsyncConnection[dict[str, Any]], user_id: str) -> bool:
        """Erase inside the caller's transaction (see Store.erase_user)."""
        cur = await conn.execute(
            "update users set wa_id_hash = 'erased:' || id::text, wa_id_enc = null, "
            "display_name_enc = null, language = null, onboarding = '{}'::jsonb, "
            "meter = '{}'::jsonb, state = 'new', opted_out_at = null, deleted_at = now() "
            "where id = %s and deleted_at is null returning id",
            (user_id,),
        )
        if await cur.fetchone() is None:
            return False
        for table in (
            "messages",
            "life_facts",
            "readings",
            "charts",
            "birth_details",
            "ad_referrals",
            "passes",
            "escalations",
            "feedback",
        ):
            await conn.execute(f"delete from {table} where user_id = %s", (user_id,))
        return True

    async def retention_sweep(
        self, now: datetime, *, opted_out_days: int, message_days: int, pending_order_hours: int
    ) -> dict[str, int]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select id::text from users where opted_out_at < %s and deleted_at is null",
                (now - timedelta(days=opted_out_days),),
            )
            due = [r["id"] for r in await cur.fetchall()]
        erased = sum([await self.erase_user(uid) for uid in due])
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "delete from messages where created_at < %s",
                (now - timedelta(days=message_days),),
            )
            dropped = cur.rowcount
            cur = await conn.execute(
                "update orders set status = 'expired' where status = 'pending' and created_at < %s",
                (now - timedelta(hours=pending_order_hours),),
            )
            expired = cur.rowcount
        return {"users_erased": erased, "messages_dropped": dropped, "orders_expired": expired}

    async def ctwa_clid(self, user_id: str) -> str | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select ctwa_clid from ad_referrals where user_id = %s and ctwa_clid is not null "
                "order by id desc limit 1",
                (user_id,),
            )
            row = await cur.fetchone()
        return None if row is None else str(row["ctwa_clid"])

    async def create_order(self, order: Order) -> None:
        o = order
        async with self._pool.connection() as conn:
            await conn.execute(
                "insert into orders (reference_id, user_id, kind, item_id, amount_paise, "
                "prashnas, days) values (%s, %s, %s, %s, %s, %s, %s) "
                "on conflict (reference_id) do nothing",
                (o.reference_id, o.user_id, o.kind, o.item_id, o.amount_paise, o.prashnas, o.days),
            )

    async def get_order(self, reference_id: str) -> Order | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"select {self._ORDER_COLS} from orders where reference_id = %s", (reference_id,)
            )
            row = await cur.fetchone()
        return None if row is None else Order(**row)

    async def has_paid(self, user_id: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select 1 from orders where user_id = %s and status = 'paid' limit 1", (user_id,)
            )
            return await cur.fetchone() is not None

    async def fulfil_order(self, reference_id: str, payment_id: str) -> Order | None:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "update orders set status = 'paid', payment_id = %s, paid_at = now() "
                f"where reference_id = %s and status <> 'paid' returning {self._ORDER_COLS}",
                (payment_id, reference_id),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            o = Order(**row)
            if o.kind == "pack":
                await conn.execute(
                    "insert into credit_ledger (user_id, delta, reason, idempotency_key, ref) "
                    "values (%s, %s, 'purchase', %s, %s) on conflict (idempotency_key) do nothing",
                    (
                        o.user_id,
                        o.prashnas,
                        f"purchase:{reference_id}",
                        Jsonb({"order": reference_id, "payment_id": payment_id}),
                    ),
                )
            else:
                await conn.execute("select 1 from users where id = %s for update", (o.user_id,))
                await conn.execute(
                    "insert into passes (user_id, plan_id, starts_at, ends_at, source) "
                    "select %s, %s, s, s + make_interval(days => %s), %s from ("
                    " select greatest(now(), coalesce(max(ends_at), now())) as s "
                    " from passes where user_id = %s) x on conflict (source) do nothing",
                    (o.user_id, o.item_id, o.days, f"payment:{reference_id}", o.user_id),
                )
        return o

    async def fail_order(self, reference_id: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "update orders set status = 'failed' where reference_id = %s "
                "and status = 'pending' returning reference_id",
                (reference_id,),
            )
            return await cur.fetchone() is not None

    async def orders_to_reconcile(self, since: datetime, limit: int) -> list[str]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select reference_id from orders where status <> 'paid' and created_at > %s "
                "order by created_at limit %s",
                (since, limit),
            )
            return [r["reference_id"] for r in await cur.fetchall()]

    async def add_pass(self, user_id: str, plan_id: str, days: int, source: str) -> Pass | None:
        async with self._pool.connection() as conn, conn.transaction():
            # One writer per user: extending from the current end must not race.
            await conn.execute("select 1 from users where id = %s for update", (user_id,))
            cur = await conn.execute(
                "insert into passes (user_id, plan_id, starts_at, ends_at, source) "
                "select %s, %s, s, s + make_interval(days => %s), %s from ("
                " select greatest(now(), coalesce(max(ends_at), now())) as s "
                " from passes where user_id = %s) x "
                "on conflict (source) do nothing returning plan_id, starts_at, ends_at",
                (user_id, plan_id, days, source, user_id),
            )
            row = await cur.fetchone()
        return None if row is None else Pass(row["plan_id"], row["starts_at"], row["ends_at"])

    _ESC_COLS = (
        "id::text, user_id::text, category, severity, status::text, alert_count, opened_at, "
        "acknowledged_at, last_alerted_at"
    )

    async def _escalations(self, where: str, args: tuple[Any, ...]) -> list[Escalation]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"select {self._ESC_COLS} from escalations where {where}", args
            )
            rows = await cur.fetchall()
        return [
            Escalation(
                r["id"],
                r["user_id"],
                r["category"],
                r["severity"],
                r["status"],
                r["alert_count"],
                r["opened_at"],
                r["acknowledged_at"],
                r["last_alerted_at"],
            )
            for r in rows
        ]

    async def active_escalation(self, user_id: str) -> Escalation | None:
        found = await self._escalations(
            "user_id = %s and status in ('open', 'acknowledged')", (user_id,)
        )
        return found[0] if found else None

    async def get_escalation(self, escalation_id: str) -> Escalation | None:
        found = await self._escalations("id = %s", (escalation_id,))
        return found[0] if found else None

    async def acknowledge_escalation(self, escalation_id: str, by: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "update escalations set status = 'acknowledged', acknowledged_at = now(), "
                "acknowledged_by = %s where id = %s and status = 'open' returning id",
                (by, escalation_id),
            )
            return await cur.fetchone() is not None

    async def mark_alerted(self, escalation_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "update escalations set alert_count = alert_count + 1, last_alerted_at = now() "
                "where id = %s",
                (escalation_id,),
            )

    async def escalations_to_reping(self, stale_before: datetime) -> list[Escalation]:
        return await self._escalations(
            "status = 'open' and severity = 1 and last_alerted_at < %s", (stale_before,)
        )

    async def resolve_escalation(self, escalation_id: str, hand_back: bool) -> bool:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "update escalations set status = %s::escalation_status, resolved_at = now() "
                "where id = %s and status in ('open', 'acknowledged') "
                "returning user_id, prior_state::text",
                ("handed_back" if hand_back else "resolved", escalation_id),
            )
            row = await cur.fetchone()
            if row is None:
                return False
            await conn.execute(  # only if still escalated (not, e.g., after STOP)
                "update users set state = %s::user_state where id = %s and state = 'escalated'",
                (row["prior_state"], row["user_id"]),
            )
        return True

    async def commit_turn(self, w: TurnWrite) -> bool:
        committed = False
        async with self._pool.connection() as conn, conn.transaction() as tx:
            for m in w.inbound:
                await conn.execute(
                    "insert into messages (user_id, direction, sent_by, wamid, turn_id, kind, "
                    "body) values (%s, 'in', 'user', %s, %s, %s, %s) "
                    "on conflict (wamid) do nothing",
                    (w.user_id, m.wamid, w.turn_id, m.kind, m.body),
                )
            # The outbound row doubles as the turn's idempotency marker.
            cur = await conn.execute(
                "insert into messages (user_id, direction, sent_by, turn_id, kind, body, meta) "
                "values (%s, 'out', 'guru', %s, %s, %s, %s) "
                "on conflict (turn_id) where direction = 'out' and turn_id is not null "
                "do nothing returning id",
                (
                    w.user_id,
                    w.turn_id,
                    "text" if w.reply_body is not None else "none",
                    w.reply_body,
                    Jsonb(w.reply_meta) if w.reply_meta is not None else None,
                ),
            )
            if await cur.fetchone() is None:
                raise Rollback(tx)  # already committed: undo the inbound inserts, skip the rest

            sets: list[str] = []
            args: list[Any] = []
            if w.state is not None:
                sets.append("state = %s::user_state")
                args.append(w.state)
            if w.language is not None:
                sets.append("language = %s")
                args.append(w.language)
            if w.onboarding is not None:
                sets.append("onboarding = %s")
                args.append(Jsonb(w.onboarding))
            if w.meter is not None:
                sets.append("meter = %s")
                args.append(Jsonb(w.meter))
            if w.opted_out is not None:
                sets.append("opted_out_at = " + ("now()" if w.opted_out else "null"))
            if w.admitted_by is not None:
                sets.append("admitted_at = coalesce(admitted_at, now())")
                sets.append("invite_code = coalesce(invite_code, %s)")
                args.append(w.admitted_by)
            if sets:
                await conn.execute(
                    f"update users set {', '.join(sets)} where id = %s", (*args, w.user_id)
                )
            if w.feedback is not None:
                await conn.execute(
                    "insert into feedback (user_id, turn_id, rating) values (%s, %s, %s) "
                    "on conflict (user_id, turn_id) do update "
                    "set rating = excluded.rating, created_at = now()",
                    (w.user_id, *w.feedback),
                )
            for c in w.consents:
                await conn.execute(
                    "insert into consents (user_id, notice_version, purpose, granted, "
                    "age_confirmed, wamid, given_at) values (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        w.user_id,
                        c.notice_version,
                        c.purpose,
                        c.granted,
                        c.age_confirmed,
                        c.wamid,
                        c.given_at,
                    ),
                )
            for cw in w.credits:
                await conn.execute(
                    "insert into credit_ledger (user_id, delta, reason, idempotency_key, ref) "
                    "values (%s, %s, %s, %s, %s) on conflict (idempotency_key) do nothing",
                    (w.user_id, cw.delta, cw.reason, cw.key, Jsonb(cw.ref) if cw.ref else None),
                )
            if w.birth is not None:
                b = w.birth
                await conn.execute(
                    "insert into birth_details (user_id, name_enc, birth_date_enc, birth_time_enc, "
                    "time_known, place_label_enc, latitude_enc, longitude_enc, tz_name) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (user_id) do update "
                    "set name_enc = excluded.name_enc, birth_date_enc = excluded.birth_date_enc, "
                    "birth_time_enc = excluded.birth_time_enc, time_known = excluded.time_known, "
                    "place_label_enc = excluded.place_label_enc, "
                    "latitude_enc = excluded.latitude_enc, longitude_enc = excluded.longitude_enc, "
                    "tz_name = excluded.tz_name",
                    (
                        w.user_id,
                        b.name_enc,
                        b.date_enc,
                        b.time_enc,
                        b.time_known,
                        b.place_enc,
                        b.latitude_enc,
                        b.longitude_enc,
                        b.tz_name,
                    ),
                )
            if w.chart is not None:
                await conn.execute(
                    "insert into charts (user_id, engine_version, dossier) values (%s, %s, %s) "
                    "on conflict (user_id, engine_version) "
                    "do update set dossier = excluded.dossier",
                    (w.user_id, w.chart[0], Jsonb(w.chart[1])),
                )
            for seq, (category, fact) in enumerate(w.facts):
                await conn.execute(
                    "insert into life_facts (user_id, category, fact, turn_id, seq) "
                    "values (%s, %s, %s, %s, %s)",
                    (w.user_id, category, fact, w.turn_id, seq),
                )
            for seq, (topic, summary, factors) in enumerate(w.readings):
                await conn.execute(
                    "insert into readings (user_id, topic, summary, factors, turn_id, seq) "
                    "values (%s, %s, %s, %s, %s, %s)",
                    (w.user_id, topic, summary, factors, w.turn_id, seq),
                )
            if w.escalation is not None:
                e = w.escalation
                await conn.execute(
                    "insert into escalations (id, user_id, category, severity, prior_state) "
                    "values (%s, %s, %s, %s, %s::user_state) on conflict (user_id) "
                    "where status in ('open', 'acknowledged') do nothing",
                    (e.id, w.user_id, e.category, e.severity, e.prior_state),
                )
            if w.referral is not None:
                r = w.referral
                await conn.execute(
                    "insert into ad_referrals (user_id, source_type, source_id, ctwa_clid, "
                    "headline, raw) values (%s, %s, %s, %s, %s, %s)",
                    (
                        w.user_id,
                        r.get("source_type"),
                        r.get("source_id"),
                        r.get("ctwa_clid"),
                        r.get("headline"),
                        Jsonb(r),
                    ),
                )
            if w.erase:
                await self._erase(conn, w.user_id)
            committed = True
        return committed

    # --- admin console -----------------------------------------------------------------

    async def get_admin(self, email: str) -> AdminUser | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select id::text, email, role::text, disabled_at is not null as disabled "
                "from admins where email = %s",
                (email.lower(),),
            )
            row = await cur.fetchone()
        return None if row is None else AdminUser(**row)

    async def add_admin(self, email: str, role: AdminRole) -> AdminUser:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "insert into admins (email, role) values (%s, %s::admin_role) "
                "on conflict (email) do update set role = excluded.role, disabled_at = null "
                "returning id::text, email, role::text, false as disabled",
                (email.lower(), role),
            )
            row = await cur.fetchone()
        assert row is not None
        return AdminUser(**row)

    # Per-user aggregates for list rows, joined laterally.
    _ROW_SELECT = (
        "select u.id::text, u.state::text, u.language, u.created_at, "
        "(select max(m.created_at) from messages m "
        " where m.user_id = u.id and m.direction = 'in') as last_inbound_at, "
        "coalesce((select sum(l.delta) from credit_ledger l where l.user_id = u.id), 0)::int "
        " as balance, "
        "exists (select 1 from charts c where c.user_id = u.id) as onboarded "
        "from users u"
    )

    @staticmethod
    def _user_row(r: dict[str, Any]) -> UserRow:
        return UserRow(
            r["id"],
            r["state"],
            r["language"],
            r["created_at"],
            r["last_inbound_at"],
            r["balance"],
            r["onboarded"],
        )

    async def list_users(
        self,
        *,
        state: UserState | None = None,
        wa_hash: str | None = None,
        before: datetime | None = None,
        limit: int = 50,
    ) -> list[UserRow]:
        where: list[str] = ["u.deleted_at is null"]
        args: list[Any] = []
        if state is not None:
            where.append("u.state = %s::user_state")
            args.append(state)
        if wa_hash is not None:
            where.append("u.wa_id_hash = %s")
            args.append(wa_hash)
        if before is not None:
            where.append("u.created_at < %s")
            args.append(before)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"{self._ROW_SELECT} where {' and '.join(where)} "
                "order by u.created_at desc limit %s",
                (*args, limit),
            )
            rows = await cur.fetchall()
        return [self._user_row(r) for r in rows]

    _ESC_ROW_COLS = (
        "e.id::text, e.user_id::text, e.category, e.severity, e.status::text, e.alert_count, "
        "e.opened_at, e.acknowledged_at, e.last_alerted_at, e.resolved_at, e.notes, "
        "u.state::text as user_state, u.language, "
        "(select max(m.created_at) from messages m "
        " where m.user_id = e.user_id and m.direction = 'in') as last_inbound_at"
    )

    async def _escalation_rows(
        self, where: str, args: tuple[Any, ...], order: str, limit: int
    ) -> list[EscalationRow]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"select {self._ESC_ROW_COLS} from escalations e join users u on u.id = e.user_id "
                f"where {where} order by {order} limit %s",
                (*args, limit),
            )
            rows = await cur.fetchall()
        return [
            EscalationRow(
                Escalation(
                    r["id"],
                    r["user_id"],
                    r["category"],
                    r["severity"],
                    r["status"],
                    r["alert_count"],
                    r["opened_at"],
                    r["acknowledged_at"],
                    r["last_alerted_at"],
                ),
                r["user_state"],
                r["language"],
                r["last_inbound_at"],
                r["resolved_at"],
                r["notes"],
            )
            for r in rows
        ]

    async def user_record(self, user_id: str) -> UserRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(f"{self._ROW_SELECT} where u.id = %s", (user_id,))
            row = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "select notice_version, purpose, granted, age_confirmed, wamid, given_at "
                "from consents where user_id = %s order by id",
                (user_id,),
            )
            consents = [Consent(**c) for c in await cur.fetchall()]
            cur = await conn.execute(
                "select delta, reason, created_at, ref from credit_ledger where user_id = %s "
                "order by id desc limit 200",
                (user_id,),
            )
            ledger = [LedgerEntry(**e) for e in await cur.fetchall()]
        escalations = await self._escalation_rows(
            "e.user_id = %s", (user_id,), "e.opened_at desc", 50
        )
        return UserRecord(
            self._user_row(row), await self.get_birth(user_id), consents, escalations, ledger
        )

    async def wa_id_enc(self, user_id: str) -> bytes | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute("select wa_id_enc from users where id = %s", (user_id,))
            row = await cur.fetchone()
        return None if row is None else row["wa_id_enc"]

    async def messages_page(
        self, user_id: str, *, before_id: int | None = None, limit: int = 50
    ) -> list[AdminMessage]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select id, direction, sent_by, kind, body, created_at, meta from messages "
                "where user_id = %s and (%s::bigint is null or id < %s) "
                "order by id desc limit %s",
                (user_id, before_id, before_id, limit),
            )
            rows = await cur.fetchall()
        return [AdminMessage(**r) for r in reversed(rows)]

    async def list_feedback(
        self, *, rating: Rating | None = None, before_id: int | None = None, limit: int = 50
    ) -> list[FeedbackRow]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select f.id, f.user_id::text, f.turn_id, f.rating, f.created_at, u.language, "
                "(select string_agg(m.body, E'\\n' order by m.id) from messages m "
                " where m.user_id = f.user_id and m.turn_id = f.turn_id and m.direction = 'in' "
                " and m.kind <> 'reaction') as question, "
                "(select m.body from messages m where m.user_id = f.user_id "
                " and m.turn_id = f.turn_id and m.direction = 'out' limit 1) as answer "
                "from feedback f join users u on u.id = f.user_id "
                "where u.deleted_at is null and (%s::text is null or f.rating = %s) "
                "and (%s::bigint is null or f.id < %s) order by f.id desc limit %s",
                (rating, rating, before_id, before_id, limit),
            )
            rows = await cur.fetchall()
        return [FeedbackRow(**r) for r in rows]

    async def set_blocked(self, user_id: str, blocked: bool) -> UserState | None:
        async with self._pool.connection() as conn:
            if blocked:
                cur = await conn.execute(
                    "update users set state = 'blocked' where id = %s "
                    "and state not in ('escalated', 'blocked') returning state::text",
                    (user_id,),
                )
            else:
                cur = await conn.execute(
                    "update users set onboarding = '{}'::jsonb, state = case when exists "
                    "(select 1 from charts c where c.user_id = users.id) "
                    "then 'active'::user_state else 'new'::user_state end "
                    "where id = %s and state = 'blocked' returning state::text",
                    (user_id,),
                )
            row = await cur.fetchone()
        return None if row is None else row["state"]

    async def list_escalations(self, *, active: bool, limit: int = 100) -> list[EscalationRow]:
        if active:
            return await self._escalation_rows(
                "e.status in ('open', 'acknowledged')", (), "e.severity, e.opened_at", limit
            )
        return await self._escalation_rows(
            "e.status in ('resolved', 'handed_back')", (), "e.resolved_at desc", limit
        )

    async def escalation_row(self, escalation_id: str) -> EscalationRow | None:
        found = await self._escalation_rows("e.id = %s", (escalation_id,), "e.opened_at", 1)
        return found[0] if found else None

    async def close_escalation(
        self, escalation_id: str, *, hand_back: bool, by: str, note: str | None
    ) -> bool:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "update escalations set status = %s::escalation_status, resolved_at = now(), "
                "resolved_by = %s, notes = %s "
                "where id = %s and status in ('open', 'acknowledged') "
                "returning user_id, prior_state::text",
                ("handed_back" if hand_back else "resolved", by, note, escalation_id),
            )
            row = await cur.fetchone()
            if row is None:
                return False
            await conn.execute(  # only if still escalated (not, e.g., after STOP)
                "update users set state = %s::user_state where id = %s and state = 'escalated'",
                (row["prior_state"], row["user_id"]),
            )
        return True

    async def log_human_message(
        self,
        user_id: str,
        turn_id: str,
        *,
        kind: str,
        body: str | None,
        meta: dict[str, Any],
        admin_id: str,
    ) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "insert into messages (user_id, direction, sent_by, turn_id, kind, body, meta, "
                "sent_by_admin) values (%s, 'out', 'human', %s, %s, %s, %s, %s) "
                "on conflict (turn_id) where direction = 'out' and turn_id is not null "
                "do nothing returning id",
                (user_id, turn_id, kind, body, Jsonb(meta), admin_id),
            )
            return await cur.fetchone() is not None

    async def add_credits(
        self,
        user_id: str,
        delta: int,
        reason: CreditReason,
        idempotency_key: str,
        ref: dict[str, Any] | None = None,
    ) -> bool:
        if delta == 0:
            raise ValueError("delta must not be zero")
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "insert into credit_ledger (user_id, delta, reason, idempotency_key, ref) "
                "values (%s, %s, %s, %s, %s) on conflict (idempotency_key) do nothing "
                "returning id",
                (user_id, delta, reason, idempotency_key, Jsonb(ref) if ref is not None else None),
            )
            return await cur.fetchone() is not None

    async def get_config(self, key: str) -> Any | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute("select value from app_config where key = %s", (key,))
            row = await cur.fetchone()
        return None if row is None else row["value"]

    async def config_entries(self) -> list[ConfigEntry]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select key, value, updated_at, updated_by from app_config order by key"
            )
            rows = await cur.fetchall()
        return [ConfigEntry(**r) for r in rows]

    async def set_config(self, key: str, value: Any, by: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "insert into app_config (key, value, updated_by) values (%s, %s, %s) "
                "on conflict (key) do update set value = excluded.value, "
                "updated_by = excluded.updated_by",
                (key, Jsonb(value), by),
            )

    async def audit(
        self,
        actor: str,
        action: str,
        subject_user_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "insert into audit_log (actor, action, subject_user_id, detail) "
                "values (%s, %s, %s, %s)",
                (actor, action, subject_user_id, Jsonb(detail) if detail is not None else None),
            )

    async def audit_entries(
        self, *, subject_user_id: str | None = None, limit: int = 100
    ) -> list[AuditEntry]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select actor, action, subject_user_id::text, detail, created_at from audit_log "
                "where (%s::uuid is null or subject_user_id = %s::uuid) "
                "order by id desc limit %s",
                (subject_user_id, subject_user_id, limit),
            )
            rows = await cur.fetchall()
        return [AuditEntry(**r) for r in rows]

    async def metrics(self, now: datetime, days: int) -> Metrics:
        since = now - timedelta(days=days)
        ist = "(%s at time zone 'Asia/Kolkata')"
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select state::text, count(*)::int as n from users where deleted_at is null "
                "group by state"
            )
            by_state = {r["state"]: r["n"] for r in await cur.fetchall()}
            cur = await conn.execute(
                "select "
                "(select count(*) from users where deleted_at is null)::int as started, "
                "(select count(distinct user_id) from consents "
                " where granted and purpose = 'readings')::int as consented, "
                "(select count(distinct user_id) from charts)::int as onboarded, "
                "(select count(distinct user_id) from credit_ledger "
                " where reason in ('purchase', 'pass'))::int as paid"
            )
            funnel = await cur.fetchone()
            assert funnel is not None
            # One row per (day, measure); days in IST.
            cur = await conn.execute(
                f"with span as (select generate_series({ist}::date - (%s - 1), {ist}::date, "
                "interval '1 day')::date as day) "
                "select s.day, "
                "(select count(*) from users u where (u.created_at at time zone 'Asia/Kolkata')"
                "::date = s.day)::int as new_users, "
                "(select count(*) from (select user_id, min(created_at) as at from charts "
                " group by user_id) c where (c.at at time zone 'Asia/Kolkata')::date = s.day)"
                "::int as onboarded, "
                "(select count(*) from messages m where m.direction = 'in' and "
                "(m.created_at at time zone 'Asia/Kolkata')::date = s.day)::int as messages_in, "
                "(select count(*) from messages m where m.direction = 'out' and "
                "(m.created_at at time zone 'Asia/Kolkata')::date = s.day)::int as messages_out, "
                "(select count(*) from escalations e where "
                "(e.opened_at at time zone 'Asia/Kolkata')::date = s.day)::int as escalations "
                "from span s order by s.day",
                (now, days, now),
            )
            day_rows = await cur.fetchall()
            cur = await conn.execute(
                "select category, count(*)::int as n from escalations "
                "where status in ('open', 'acknowledged') group by category"
            )
            open_escalations = {r["category"]: r["n"] for r in await cur.fetchall()}
            cur = await conn.execute(
                "select percentile_cont(0.5) within group (order by extract(epoch from "
                "acknowledged_at - opened_at) / 60) as m from escalations "
                "where acknowledged_at is not null and opened_at >= %s",
                (since,),
            )
            ack = await cur.fetchone()
            cur = await conn.execute(
                "select r.source_id, count(distinct r.user_id)::int as users, "
                "count(distinct c.user_id)::int as onboarded, "
                "count(distinct l.user_id)::int as paid "
                "from ad_referrals r "
                "left join charts c on c.user_id = r.user_id "
                "left join credit_ledger l on l.user_id = r.user_id "
                " and l.reason in ('purchase', 'pass') "
                "where r.source_id is not null "
                "group by r.source_id order by users desc, r.source_id limit 20"
            )
            ads = [AdStats(**r) for r in await cur.fetchall()]
        days_out = [
            DayStats(
                day=r["day"] if isinstance(r["day"], date) else date.fromisoformat(str(r["day"])),
                new_users=r["new_users"],
                onboarded=r["onboarded"],
                messages_in=r["messages_in"],
                messages_out=r["messages_out"],
                escalations=r["escalations"],
            )
            for r in day_rows
        ]
        median = ack["m"] if ack is not None else None
        return Metrics(
            users_by_state=by_state,
            funnel=dict(funnel),
            days=days_out,
            open_escalations=open_escalations,
            median_ack_minutes=float(median) if median is not None else None,
            ads=ads,
        )
