"""`python -m guruji migrate`: which migrations still need applying, in order."""

from pathlib import Path

import pytest

from guruji.db.migrate import MigrationError, pending

REPO_MIGRATIONS = Path(__file__).parents[2] / "supabase" / "migrations"


def test_pending_in_version_order(tmp_path: Path) -> None:
    for name in ("20260102_b.sql", "20260101_a.sql", "20260103_c.sql", "notes.txt"):
        (tmp_path / name).write_text("select 1;")
    assert [f.name for f in pending(tmp_path, set())] == [
        "20260101_a.sql",
        "20260102_b.sql",
        "20260103_c.sql",
    ]
    assert [f.name for f in pending(tmp_path, {"20260101", "20260102"})] == ["20260103_c.sql"]
    with pytest.raises(MigrationError):
        pending(tmp_path / "missing", set())


def test_repo_migration_versions_are_unique() -> None:
    versions = [f.stem.partition("_")[0] for f in REPO_MIGRATIONS.glob("*.sql")]
    assert versions and len(versions) == len(set(versions))
