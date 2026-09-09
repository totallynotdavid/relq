"""SQLite migration lifecycle and locking contracts."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from relq_migrate import FileMigrationProvider, MigrationStatus, SQLiteMigrator


def _write(folder: Path, name: str, sql: str) -> None:
    (folder / name).write_text(sql, encoding="utf-8")


def test_migrations_apply_in_order_and_are_idempotent(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_create_users.sql",
        "create table users (id integer primary key, name text not null);",
    )
    _write(
        migrations,
        "0002_add_status.sql",
        "alter table users add column status text not null default 'new';",
    )
    database = sqlite3.connect(tmp_path / "app.db")
    try:
        migrator = SQLiteMigrator(database, FileMigrationProvider(migrations))
        first = migrator.migrate_to_latest()
        second = migrator.migrate_to_latest()

        assert first.error is None
        assert [result.status for result in first.results] == [
            MigrationStatus.SUCCESS,
            MigrationStatus.SUCCESS,
        ]
        assert second.error is None
        assert second.results == ()
        assert database.execute("pragma table_info(users)").fetchall()[-1][1] == "status"
        assert database.execute(
            "select migration_name from relq_migrations order by migration_name"
        ).fetchall() == [("0001_create_users.sql",), ("0002_add_status.sql",)]
    finally:
        database.close()


def test_same_line_scripts_split_quoted_comments_and_triggers(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_same_line.sql",
        '/* comment; */ create table "semi;table" (value text); '
        "create table source (id integer); create table audit (id integer, marker text); "
        "create trigger source_audit after insert on source begin "
        "insert into audit values (new.id, 'inserted'); "
        "update audit set marker = 'updated' where id = new.id; end; "
        "insert into \"semi;table\" values ('value;inside'); insert into source values (1);",
    )
    database = sqlite3.connect(":memory:")
    try:
        report = SQLiteMigrator(database, FileMigrationProvider(migrations)).migrate_to_latest()
        assert report.error is None
        assert database.execute('select value from "semi;table"').fetchall() == [("value;inside",)]
        assert database.execute("select * from audit").fetchall() == [(1, "updated")]
    finally:
        database.close()


def test_concurrent_migrators_do_not_double_apply(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_create_events.sql",
        "create table events (id integer primary key, label text not null);",
    )
    _write(migrations, "0002_insert_once.sql", "insert into events (label) values ('once');")
    database_path = tmp_path / "concurrent.db"

    def run() -> tuple[object, tuple[object, ...]]:
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            report = SQLiteMigrator(
                connection, FileMigrationProvider(migrations)
            ).migrate_to_latest()
            return report.error, tuple(result.status for result in report.results)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(run)
        second_future = executor.submit(run)
        first = first_future.result()
        second = second_future.result()

    assert first[0] is None
    assert second[0] is None
    statuses = first[1] + second[1]
    assert statuses.count(MigrationStatus.SUCCESS) == 2
    assert all(
        status in (MigrationStatus.SUCCESS, MigrationStatus.NOT_EXECUTED) for status in statuses
    )
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("select count(*) from events").fetchone() == (1,)
    finally:
        connection.close()


def test_failed_migration_reports_error_and_not_executed_files(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0001_create_users.sql", "create table users (id integer);")
    _write(migrations, "0002_broken.sql", "this is not valid sql;")
    _write(migrations, "0003_never_reached.sql", "create table never_reached (id integer);")
    database = sqlite3.connect(":memory:")
    try:
        report = SQLiteMigrator(database, FileMigrationProvider(migrations)).migrate_to_latest()
        assert report.error is not None
        assert [result.status for result in report.results] == [
            MigrationStatus.SUCCESS,
            MigrationStatus.ERROR,
            MigrationStatus.NOT_EXECUTED,
        ]
        assert database.execute(
            "select name from sqlite_master where type = 'table' and name = 'users'"
        ).fetchall() == [("users",)]
    finally:
        database.close()


def test_rejects_migration_transaction_control(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_owns_transaction.sql",
        "create table leaked (id integer); end transaction;",
    )
    database = sqlite3.connect(":memory:")
    try:
        migrator = SQLiteMigrator(database, FileMigrationProvider(migrations))
        report = migrator.migrate_to_latest()
        assert report.error is not None
        assert "ended the migrator's transaction" in str(report.error)
        assert database.execute(
            "select name from sqlite_master where type = 'table' and name = 'leaked'"
        ).fetchall() == [("leaked",)]
        assert database.execute("select count(*) from relq_migrations").fetchone() == (0,)

        retry = migrator.migrate_to_latest()
        assert retry.error is not None
        assert database.execute(
            "select name from sqlite_master where type = 'table' and name = 'leaked'"
        ).fetchall() == [("leaked",)]
    finally:
        database.close()
