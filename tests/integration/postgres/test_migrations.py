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


async def test_empty_and_comment_only_migrations_are_successful(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0001_empty.sql", "\n;\n")
    _write(migrations, "0002_comment.sql", "-- placeholder\n")
    _write(migrations, "0003_create_users.sql", "create table empty_migration_users (id integer);")

    report = await PostgresMigrator(
        postgres_admin, FileMigrationProvider(migrations)
    ).migrate_to_latest()

    assert report.error is None
    assert [result.status for result in report.results] == [
        MigrationStatus.SUCCESS,
        MigrationStatus.SUCCESS,
        MigrationStatus.SUCCESS,
    ]
    assert await postgres_admin.fetchval("select count(*) from relq_migrations") == 3


async def test_rejects_malformed_history_checksum(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    await postgres_admin.execute(
        """
        create table relq_migrations (
            migration_name text primary key,
            checksum text,
            applied_at timestamptz not null default now()
        )
        """
    )
    await postgres_admin.execute(
        "insert into relq_migrations (migration_name, checksum) values ($1, NULL)",
        "0001_malformed.sql",
    )
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_malformed.sql",
        "create table malformed_history_users (id integer);",
    )

    report = await PostgresMigrator(
        postgres_admin, FileMigrationProvider(migrations)
    ).migrate_to_latest()

    assert report.error is not None
    assert "malformed" in str(report.error)
    assert report.results == ()
    assert await postgres_admin.fetchval("select to_regclass('malformed_history_users')") is None


async def test_sql_standard_atomic_procedure_body_is_one_migration_statement(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_create_atomic_procedure.sql",
        """
        create table atomic_procedure_rows (id integer primary key);
        create procedure atomic_procedure() language sql begin atomic
            insert into atomic_procedure_rows values (1);
            insert into atomic_procedure_rows values (2);
        end;
        """,
    )

    report = await PostgresMigrator(
        postgres_admin, FileMigrationProvider(migrations)
    ).migrate_to_latest()

    assert report.error is None
    assert [result.status for result in report.results] == [MigrationStatus.SUCCESS]
    assert await postgres_admin.fetchval("select count(*) from relq_migrations") == 1
    assert (
        await postgres_admin.fetchval(
            "select count(*) from pg_proc where proname = 'atomic_procedure' and prokind = 'p'"
        )
        == 1
    )


async def test_history_reference_survives_a_migration_search_path_change(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_change_search_path.sql",
        "set search_path = pg_temp; create table search_path_users (id integer primary key);",
    )
    harness = configured_harness()

    report = await PostgresMigrator(
        postgres_admin, FileMigrationProvider(migrations)
    ).migrate_to_latest()
    retry = await PostgresMigrator(
        postgres_admin, FileMigrationProvider(migrations)
    ).migrate_to_latest()

    assert report.error is None
    assert retry.error is None
    assert retry.results == ()
    assert await postgres_admin.fetchval("select to_regclass('pg_temp.search_path_users')") == (
        "search_path_users"
    )
    assert (
        await postgres_admin.fetchval(f'select count(*) from "{harness.schema}"."relq_migrations"')
        == 1
    )


async def test_splits_plain_escaped_strings_when_standard_conforming_strings_is_off(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_legacy_string.sql",
        r"select 'escaped \''; create table legacy_string_users (id integer);",
    )

    await postgres_admin.execute("SET standard_conforming_strings = off")
    try:
        report = await PostgresMigrator(
            postgres_admin, FileMigrationProvider(migrations)
        ).migrate_to_latest()
    finally:
        await postgres_admin.execute("SET standard_conforming_strings = on")

    assert report.error is None
    assert [result.status for result in report.results] == [MigrationStatus.SUCCESS]
    assert await postgres_admin.fetchval("select to_regclass('legacy_string_users')") == (
        "legacy_string_users"
    )


async def test_detects_set_config_string_mode_before_hidden_transaction_control(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_set_config_string_mode.sql",
        r"""
        create table set_config_string_mode_users (id integer);
        select set_config('standard_conforming_strings', 'off', false);
        select 'escaped \''; commit;
        """,
    )

    report = await PostgresMigrator(
        postgres_admin, FileMigrationProvider(migrations)
    ).migrate_to_latest()

    assert report.error is not None
    assert (
        await postgres_admin.fetchval("select to_regclass('set_config_string_mode_users')") is None
    )
    assert await postgres_admin.fetchval("select count(*) from relq_migrations") == 0


async def test_rejects_migration_local_standard_conforming_strings_change(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_changes_string_mode.sql",
        "set standard_conforming_strings = off; "
        "create table rejected_string_mode_users (id integer);",
    )

    report = await PostgresMigrator(
        postgres_admin, FileMigrationProvider(migrations)
    ).migrate_to_latest()

    assert report.error is not None
    assert "standard_conforming_strings" in str(report.error)
    assert await postgres_admin.fetchval("select to_regclass('rejected_string_mode_users')") is None
    assert await postgres_admin.fetchval("select count(*) from relq_migrations") == 0


async def test_rejects_quoted_migration_local_standard_conforming_strings_change(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_changes_quoted_string_mode.sql",
        'set "standard_conforming_strings" = off; '
        "create table rejected_quoted_string_mode_users (id integer);",
    )

    report = await PostgresMigrator(
        postgres_admin, FileMigrationProvider(migrations)
    ).migrate_to_latest()

    assert report.error is not None
    assert "standard_conforming_strings" in str(report.error)
    assert (
        await postgres_admin.fetchval("select to_regclass('rejected_quoted_string_mode_users')")
        is None
    )
    assert await postgres_admin.fetchval("select count(*) from relq_migrations") == 0


@pytest.mark.parametrize(
    "reset_statement",
    [
        "reset standard_conforming_strings",
        "reset all",
    ],
)
async def test_rejects_migration_standard_conforming_strings_reset(
    postgres_admin: asyncpg.Connection, tmp_path: Path, reset_statement: str
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_resets_string_mode.sql",
        f"{reset_statement}; create table rejected_reset_string_mode_users (id integer);",
    )

    report = await PostgresMigrator(
        postgres_admin, FileMigrationProvider(migrations)
    ).migrate_to_latest()

    assert report.error is not None
    assert "standard_conforming_strings" in str(report.error)
    assert (
        await postgres_admin.fetchval("select to_regclass('rejected_reset_string_mode_users')")
        is None
    )
    assert await postgres_admin.fetchval("select count(*) from relq_migrations") == 0


async def test_history_reference_preserves_mixed_case_table_name(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_mixed_case_history.sql",
        "create table mixed_case_history_users (id integer);",
    )

    report = await PostgresMigrator(
        postgres_admin,
        FileMigrationProvider(migrations),
        table_name="MyMigrations",
    ).migrate_to_latest()

    assert report.error is None
    assert await postgres_admin.fetchval('select count(*) from "MyMigrations"') == 1
    assert await postgres_admin.fetchval("select to_regclass('mymigrations')") is None


async def test_rejects_history_table_names_over_postgres_identifier_limit(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="63-byte"):
        PostgresMigrator(
            postgres_admin,
            FileMigrationProvider(tmp_path),
            table_name="a" * 64,
        )
    assert await postgres_admin.fetchval("select 1") == 1


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
    assert "transaction-control SQL" in str(report.error)
    assert await postgres_admin.fetchval("select to_regclass('postgres_leaked')") is None
    assert await postgres_admin.fetchval("select count(*) from relq_migrations") == 0


async def test_rejects_embedded_savepoint_control(
    postgres_admin: asyncpg.Connection, tmp_path: Path
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_owns_savepoint.sql",
        "savepoint migration_work; create table postgres_savepoint_leaked (id integer); "
        "rollback to migration_work;",
    )

    report = await PostgresMigrator(
        postgres_admin, FileMigrationProvider(migrations)
    ).migrate_to_latest()

    assert report.error is not None
    assert "transaction-control SQL" in str(report.error)
    assert await postgres_admin.fetchval("select to_regclass('postgres_savepoint_leaked')") is None
    assert await postgres_admin.fetchval("select count(*) from relq_migrations") == 0


async def test_rejects_transaction_control_before_running_migration(
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
    assert "transaction-control SQL" in str(report.error)
    assert await postgres_admin.fetchval("select to_regclass('postgres_committed')") is None
    assert await postgres_admin.fetchval("select to_regclass('postgres_unrecorded')") is None
    assert await postgres_admin.fetchval("select count(*) from relq_migrations") == 0
