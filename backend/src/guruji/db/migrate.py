"""Apply supabase/migrations/*.sql in order: `python -m guruji migrate`.

Each file runs in its own transaction and is recorded where the Supabase CLI records
migrations (supabase_migrations.schema_migrations), so the CLI and this command agree
on what has been applied. Already-applied versions are skipped; run it on every deploy.
"""

import logging
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)

_TRACK = "supabase_migrations.schema_migrations"


class MigrationError(RuntimeError):
    pass


def pending(directory: Path, applied: set[str]) -> list[Path]:
    files = sorted(directory.glob("*.sql"))
    if not files:
        raise MigrationError(f"no migrations in {directory}")
    return [f for f in files if f.stem.partition("_")[0] not in applied]


def migrate(url: str, directory: Path) -> list[str]:
    """The names of the migrations applied now (empty: already up to date)."""
    with psycopg.connect(url, prepare_threshold=None, autocommit=True) as conn:
        conn.execute("create schema if not exists supabase_migrations")
        conn.execute(
            f"create table if not exists {_TRACK} "
            "(version text primary key, statements text[], name text)"
        )
        applied = {r[0] for r in conn.execute(f"select version from {_TRACK}")}
        todo = pending(directory, applied)
        existing = conn.execute("select to_regclass('public.users')").fetchone()
        if todo and not applied and existing is not None and existing[0] is not None:
            # Tables but no record of them (e.g. created by Postgres' initdb in compose):
            # re-running the first migration would fail half-way. Refuse, clearly.
            raise MigrationError(
                "the schema exists but no migrations are recorded; record the applied "
                f"versions in {_TRACK} before running migrate"
            )
        done: list[str] = []
        for f in todo:
            version, _, name = f.stem.partition("_")
            sql = f.read_text("utf-8")
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    f"insert into {_TRACK} (version, statements, name) values (%s, %s, %s)",
                    (version, [sql], name),
                )
            log.info("migration applied: %s", f.name)
            done.append(f.name)
        return done
