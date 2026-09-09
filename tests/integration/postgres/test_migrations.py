"""PostgreSQL migration lifecycle and advisory-lock contracts."""

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest
from relq_migrate import FileMigrationProvider, MigrationStatus, PostgresMigrator

from tests.integration.postgres.support import configured_harness

pytestmark = pytest.mark.skipif(
    os.environ.get("RELQ_TEST_POSTGRES_DSN") is None,
    reason="set RELQ_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)


def _write(folder: Path, name: str, sql: str) -> None:
    (folder / name).write_text(sql, encoding="utf-8")


async def _connect() -> asyncpg.Connection:
    harness = configured_harness()
    connection = await asyncpg.connect(harness.dsn)
    await connection.execute(
        "select set_config('search_path', $1, false)", f"{harness.schema}, public"
    )
    return connection


async def test_migrations_apply_in_order_and_are_idempotent(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_create_users.sql",
        "create table migration_users (id integer primary key, name text not null);",
    )
    _write(
        migrations,
        "0002_add_status.sql",
        "alter table migration_users add column status text not null default 'new';",
    )
    migrator = PostgresMigrator(postgres_admin, FileMigrationProvider(migrations))
    first = await migrator.migrate_to_latest()
    second = await migrator.migrate_to_latest()

    assert first.error is None
    assert [result.status for result in first.results] == [
        MigrationStatus.SUCCESS,
        MigrationStatus.SUCCESS,
    ]
    assert second.error is None
    assert second.results == ()
    assert await postgres_admin.fetchval("select count(*) from relq_migrations") == 2
    assert (
        await postgres_admin.fetchval(
            "select column_name from information_schema.columns "
            "where table_schema = current_schema() and table_name = 'migration_users' "
            "and column_name = 'status'"
        )
        == "status"
    )


async def test_advisory_lock_prevents_concurrent_double_apply(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_create_events.sql",
        "select pg_sleep(0.1); create table migration_events (id integer primary key);",
    )
    _write(migrations, "0002_insert_once.sql", "insert into migration_events values (1);")
    first = await _connect()
    second = await _connect()
    try:
        provider = FileMigrationProvider(migrations)
        reports = await asyncio.gather(
            PostgresMigrator(first, provider).migrate_to_latest(),
            PostgresMigrator(second, provider).migrate_to_latest(),
        )
        assert all(report.error is None for report in reports), [report.error for report in reports]
        assert sorted(
            (tuple(result.status for result in report.results) for report in reports), key=len
        ) == [
            (),
            (MigrationStatus.SUCCESS, MigrationStatus.SUCCESS),
        ]
        assert await first.fetchval("select count(*) from migration_events") == 1
    finally:
        await first.close()
        await second.close()


async def test_rejects_caller_owned_transaction_before_reporting_success(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_create_users.sql",
        "create table caller_transaction_users (id integer primary key);",
    )
    migrator = PostgresMigrator(postgres_admin, FileMigrationProvider(migrations))

    with pytest.raises(RuntimeError, match="caller-owned transaction"):
        async with postgres_admin.transaction():
            await migrator.migrate_to_latest()

    assert await postgres_admin.fetchval("select to_regclass('relq_migrations')") is None
    assert await postgres_admin.fetchval("select to_regclass('caller_transaction_users')") is None


async def test_rejects_embedded_transaction_control(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_owns_transaction.sql",
        "create table postgres_leaked (id integer); commit;",
    )
    migrator = PostgresMigrator(postgres_admin, FileMigrationProvider(migrations))

    report = await migrator.migrate_to_latest()

    assert report.error is not None
    assert [result.status for result in report.results] == [MigrationStatus.ERROR]
    assert "ended the migrator's transaction" in str(report.error)
    assert await postgres_admin.fetchval("select to_regclass('postgres_leaked')") == (
        "postgres_leaked"
    )
    assert await postgres_admin.fetchval("select count(*) from relq_migrations") == 0


async def test_rejects_transaction_replacement_even_when_active(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_replaces_transaction.sql",
        "create table postgres_committed (id integer); "
        "commit; begin; create table postgres_unrecorded (id integer);",
    )
    migrator = PostgresMigrator(postgres_admin, FileMigrationProvider(migrations))

    report = await migrator.migrate_to_latest()

    assert report.error is not None
    assert "replaced the migrator's transaction" in str(report.error)
    assert await postgres_admin.fetchval("select to_regclass('postgres_committed')") == (
        "postgres_committed"
    )
    assert await postgres_admin.fetchval("select to_regclass('postgres_unrecorded')") is None
    assert await postgres_admin.fetchval("select count(*) from relq_migrations") == 0
