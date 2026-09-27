"""Postgres `Store` (Supabase, via the Supavisor pooler in transaction mode).

Transaction mode cannot keep prepared statements across checkouts, so they are off.
"""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection, Rollback
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from guruji.db.models import (
    EncryptedBirth,
    Escalation,
    LifeFact,
    LoggedMessage,
    Reading,
    StoredReply,
    TurnWrite,
    User,
)

_USER_COLS = "id::text, state::text, language, onboarding, created_at"


def _user(row: dict[str, Any]) -> User:
    return User(
        id=row["id"],
        state=row["state"],
        language=row["language"],
        onboarding=row["onboarding"] or {},
        created_at=row["created_at"],
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
            await conn.execute(
                "update users set state = %s::user_state where id = %s",
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
            if sets:
                await conn.execute(
                    f"update users set {', '.join(sets)} where id = %s", (*args, w.user_id)
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
            committed = True
        return committed
