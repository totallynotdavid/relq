"""SQLite migration lifecycle and locking contracts."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from relq_migrate import FileMigrationProvider, MigrationStatus, SQLiteMigrator


def _write(folder: Path, name: str, sql: str) -> None:
    (folder / name).write_text(sql, encoding="utf-8")


def _dict_row_factory(cursor: sqlite3.Cursor, row: tuple[object, ...]) -> dict[str, object]:
    description = cursor.description
    assert description is not None
    return {str(column[0]): value for column, value in zip(description, row)}


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


def test_autocommit_true_uses_explicit_transaction_boundaries(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0001_create_users.sql", "create table users (id integer);")
    database = sqlite3.connect(tmp_path / "app.db", autocommit=True)
    try:
        report = SQLiteMigrator(database, FileMigrationProvider(migrations)).migrate_to_latest()

        assert report.error is None
        assert database.in_transaction is False
        assert database.execute(
            "select name from sqlite_master where name = 'users'"
        ).fetchall() == [("users",)]
    finally:
        database.close()


def test_history_reads_ignore_connection_row_factory(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0001_create_users.sql", "create table users (id integer);")
    _write(migrations, "0002_create_groups.sql", "create table groups (id integer);")
    database = sqlite3.connect(":memory:")
    database.row_factory = _dict_row_factory
    try:
        migrator = SQLiteMigrator(database, FileMigrationProvider(migrations))
        report = migrator.migrate_to_latest()
        retry = migrator.migrate_to_latest()

        assert report.error is None
        assert [result.status for result in report.results] == [
            MigrationStatus.SUCCESS,
            MigrationStatus.SUCCESS,
        ]
        assert retry.error is None
        assert retry.results == ()
    finally:
        database.close()


def test_history_reads_decode_connection_bytes_text_factory(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0001_create_users.sql", "create table users (id integer);")
    _write(migrations, "0002_create_groups.sql", "create table groups (id integer);")
    database = sqlite3.connect(":memory:")
    database.text_factory = bytes
    try:
        migrator = SQLiteMigrator(database, FileMigrationProvider(migrations))
        report = migrator.migrate_to_latest()
        retry = migrator.migrate_to_latest()

        assert report.error is None
        assert [result.status for result in report.results] == [
            MigrationStatus.SUCCESS,
            MigrationStatus.SUCCESS,
        ]
        assert retry.error is None
        assert retry.results == ()
    finally:
        database.close()


def test_empty_and_comment_only_migrations_are_successful(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0001_empty.sql", "\n;\n")
    _write(migrations, "0002_comment.sql", "-- placeholder\n")
    _write(migrations, "0003_create_users.sql", "create table users (id integer);")
    database = sqlite3.connect(":memory:")
    try:
        report = SQLiteMigrator(database, FileMigrationProvider(migrations)).migrate_to_latest()

        assert report.error is None
        assert [result.status for result in report.results] == [
            MigrationStatus.SUCCESS,
            MigrationStatus.SUCCESS,
            MigrationStatus.SUCCESS,
        ]
        assert database.execute("select count(*) from relq_migrations").fetchone() == (3,)
    finally:
        database.close()


def test_provider_errors_are_returned_in_the_migration_report(tmp_path: Path) -> None:
    database = sqlite3.connect(":memory:")

    try:
        report = SQLiteMigrator(
            database, FileMigrationProvider(tmp_path / "missing-migrations")
        ).migrate_to_latest()

        assert isinstance(report.error, NotADirectoryError)
        assert report.results == ()
    finally:
        database.close()


def test_autocommit_false_is_rejected_as_unsupported(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    database = sqlite3.connect(":memory:", autocommit=False)
    try:
        with pytest.raises(ValueError, match="autocommit=False"):
            SQLiteMigrator(database, FileMigrationProvider(migrations))
    finally:
        database.close()


def test_caller_owned_transaction_is_rejected_consistently(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    database = sqlite3.connect(":memory:")
    try:
        database.execute("BEGIN")
        with pytest.raises(RuntimeError, match="caller-owned transaction"):
            SQLiteMigrator(database, FileMigrationProvider(migrations)).migrate_to_latest()
        database.rollback()
    finally:
        database.close()


def test_existing_authorizer_is_preserved(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0001_create_users.sql", "create table users (id integer);")
    database = sqlite3.connect(":memory:")

    def deny_delete(
        action: int,
        _arg1: str | None,
        _arg2: str | None,
        _database_name: str | None,
        _source: str | None,
    ) -> int:
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DELETE else sqlite3.SQLITE_OK

    database.set_authorizer(deny_delete)
    try:
        report = SQLiteMigrator(database, FileMigrationProvider(migrations)).migrate_to_latest()

        assert report.error is None
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            database.execute("delete from users")
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
        assert "transaction-control SQL" in str(report.error)
        assert (
            database.execute(
                "select name from sqlite_master where type = 'table' and name = 'leaked'"
            ).fetchall()
            == []
        )
        assert database.execute("select count(*) from relq_migrations").fetchone() == (0,)

        retry = migrator.migrate_to_latest()
        assert retry.error is not None
        assert (
            database.execute(
                "select name from sqlite_master where type = 'table' and name = 'leaked'"
            ).fetchall()
            == []
        )
    finally:
        database.close()


def test_rejects_migration_savepoint_control(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001_owns_savepoint.sql",
        "savepoint migration_work; create table leaked (id integer); rollback to migration_work;",
    )
    database = sqlite3.connect(":memory:")
    try:
        report = SQLiteMigrator(database, FileMigrationProvider(migrations)).migrate_to_latest()

        assert report.error is not None
        assert "transaction-control SQL" in str(report.error)
        assert (
            database.execute(
                "select name from sqlite_master where type = 'table' and name = 'leaked'"
            ).fetchall()
            == []
        )
        assert database.execute("select count(*) from relq_migrations").fetchone() == (0,)
    finally:
        database.close()
