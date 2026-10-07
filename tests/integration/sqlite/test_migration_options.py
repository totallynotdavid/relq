"""SQLite migrator options: foreign-key-free rebuilds and the busy timeout."""

import sqlite3
import threading
from pathlib import Path
from typing import cast

import pytest
from relq_migrate import FileMigrationProvider, MigrationError, MigrationStatus, SQLiteMigrator

_SCHEMA = """
create table parents (id integer primary key, name text not null);
create table children (
    id integer primary key,
    parent_id integer not null references parents (id) on delete cascade
);
insert into parents (id, name) values (1, 'a');
insert into children (id, parent_id) values (10, 1), (11, 1);
"""


def _migrations(folder: Path, **files: str) -> FileMigrationProvider:
    folder.mkdir(exist_ok=True)
    for name, sql in files.items():
        (folder / f"{name}.sql").write_text(sql, encoding="utf-8")
    return FileMigrationProvider(folder)


def _pragma(connection: sqlite3.Connection, name: str) -> object:
    row = cast(tuple[object], connection.execute(f"pragma {name}").fetchone())
    return row[0]


def _column_names(connection: sqlite3.Connection, table: str) -> list[object]:
    rows = cast(list[tuple[object, ...]], connection.execute(f"pragma table_info({table})"))
    return [row[1] for row in rows]


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, autocommit=True)
    connection.execute("pragma foreign_keys = on")
    return connection


_REBUILD = """-- relq: foreign_keys = off
create table parents_new (id integer primary key, name text not null, note text);
insert into parents_new (id, name) select id, name from parents;
drop table parents;
alter table parents_new rename to parents;
"""


def test_rebuild_with_foreign_keys_off_keeps_cascade_children(tmp_path: Path) -> None:
    provider = _migrations(tmp_path / "m", **{"0001_schema": _SCHEMA, "0002_rebuild": _REBUILD})
    database = _connect(tmp_path / "app.db")
    try:
        report = SQLiteMigrator(database, provider).migrate_to_latest()

        assert report.error is None
        assert [result.status for result in report.results] == [MigrationStatus.SUCCESS] * 2
        assert database.execute("select id from children order by id").fetchall() == [(10,), (11,)]
        assert _column_names(database, "parents") == ["id", "name", "note"]
        assert _pragma(database, "foreign_keys") == 1
    finally:
        database.close()


def test_the_same_rebuild_with_enforcement_on_cascades_the_children_away(tmp_path: Path) -> None:
    # Foreign-key enforcement must stay on here to expose DROP TABLE's implicit DELETE.
    rebuild = _REBUILD.removeprefix("-- relq: foreign_keys = off\n")
    provider = _migrations(tmp_path / "m", **{"0001_schema": _SCHEMA, "0002_rebuild": rebuild})
    database = _connect(tmp_path / "app.db")
    try:
        report = SQLiteMigrator(database, provider).migrate_to_latest()

        assert report.error is None
        assert database.execute("select count(*) from children").fetchone() == (0,)
    finally:
        database.close()


def test_foreign_key_violations_roll_the_migration_back(tmp_path: Path) -> None:
    broken = """-- relq: foreign_keys = off
delete from parents;
"""
    provider = _migrations(tmp_path / "m", **{"0001_schema": _SCHEMA, "0002_broken": broken})
    database = _connect(tmp_path / "app.db")
    try:
        report = SQLiteMigrator(database, provider).migrate_to_latest()

        assert report.error is not None
        assert "0002_broken.sql" in str(report.error)
        assert "foreign key violation" in str(report.error)
        assert [result.status for result in report.results] == [
            MigrationStatus.SUCCESS,
            MigrationStatus.ERROR,
        ]
        assert database.execute("select count(*) from parents").fetchone() == (1,)
        assert database.execute("select migration_name from relq_migrations").fetchall() == [
            ("0001_schema.sql",)
        ]
        assert _pragma(database, "foreign_keys") == 1
        assert database.in_transaction is False
    finally:
        database.close()


def test_enforcement_stays_off_for_connections_that_had_it_off(tmp_path: Path) -> None:
    provider = _migrations(tmp_path / "m", **{"0001_schema": _SCHEMA, "0002_rebuild": _REBUILD})
    database = sqlite3.connect(tmp_path / "app.db", autocommit=True)
    try:
        assert _pragma(database, "foreign_keys") == 0

        assert SQLiteMigrator(database, provider).migrate_to_latest().error is None
        assert _pragma(database, "foreign_keys") == 0
    finally:
        database.close()


def test_legacy_transaction_control_connections_can_rebuild_too(tmp_path: Path) -> None:
    provider = _migrations(tmp_path / "m", **{"0001_schema": _SCHEMA, "0002_rebuild": _REBUILD})
    database = sqlite3.connect(tmp_path / "app.db")
    database.execute("pragma foreign_keys = on")
    try:
        assert SQLiteMigrator(database, provider).migrate_to_latest().error is None
        assert database.execute("select count(*) from children").fetchone() == (2,)
        assert _pragma(database, "foreign_keys") == 1
    finally:
        database.close()


def test_pending_names_unapplied_migrations_and_writes_nothing(tmp_path: Path) -> None:
    provider = _migrations(
        tmp_path / "m", **{"0001_a": "create table a (id integer);", "0002_b": "select 1;"}
    )
    path = tmp_path / "app.db"
    database = sqlite3.connect(path, autocommit=True)
    try:
        migrator = SQLiteMigrator(database, provider)

        assert migrator.pending() == ("0001_a.sql", "0002_b.sql")
        assert database.execute("select name from sqlite_master").fetchall() == []

        assert migrator.migrate_to_latest().error is None
        assert migrator.pending() == ()
    finally:
        database.close()


def test_pending_works_on_a_read_only_connection_and_rejects_a_changed_migration(
    tmp_path: Path,
) -> None:
    provider = _migrations(tmp_path / "m", **{"0001_a": "create table a (id integer);"})
    path = tmp_path / "app.db"
    writer = sqlite3.connect(path, autocommit=True)
    try:
        assert SQLiteMigrator(writer, provider).migrate_to_latest().error is None
    finally:
        writer.close()
    _migrations(tmp_path / "m", **{"0001_a": "create table a (id integer, extra text);"})
    reader = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, autocommit=True)
    try:
        with pytest.raises(MigrationError, match="modified after it was applied"):
            SQLiteMigrator(reader, provider).pending()
    finally:
        reader.close()


def test_busy_timeout_is_set_for_the_run_and_restored_after(tmp_path: Path) -> None:
    provider = _migrations(tmp_path / "m", **{"0001_a": "create table a (id integer);"})
    database = sqlite3.connect(tmp_path / "app.db", autocommit=True)
    try:
        database.execute("pragma busy_timeout = 1234")

        report = SQLiteMigrator(database, provider, busy_timeout=2.5).migrate_to_latest()

        assert report.error is None
        assert _pragma(database, "busy_timeout") == 1234
    finally:
        database.close()


def test_busy_timeout_bounds_the_wait_for_another_writer(tmp_path: Path) -> None:
    provider = _migrations(tmp_path / "m", **{"0001_a": "create table a (id integer);"})
    path = tmp_path / "app.db"
    holder = sqlite3.connect(path, autocommit=True)
    holder.execute("create table seed (id integer)")
    holder.execute("begin immediate")
    waiter = sqlite3.connect(path, autocommit=True)
    try:
        report = SQLiteMigrator(waiter, provider, busy_timeout=0.05).migrate_to_latest()

        assert report.error is not None
        assert "locked" in str(report.error)
    finally:
        holder.execute("rollback")
        holder.close()
        waiter.close()


def test_busy_timeout_waits_for_a_slow_writer_to_finish(tmp_path: Path) -> None:
    provider = _migrations(tmp_path / "m", **{"0001_a": "create table a (id integer);"})
    path = tmp_path / "app.db"
    holder = sqlite3.connect(path, autocommit=True, check_same_thread=False)
    holder.execute("create table seed (id integer)")
    holder.execute("begin immediate")
    release = threading.Timer(0.3, lambda: holder.execute("rollback"))
    waiter = sqlite3.connect(path, autocommit=True, check_same_thread=False)
    try:
        release.start()
        report = SQLiteMigrator(waiter, provider, busy_timeout=10).migrate_to_latest()

        assert report.error is None
        assert waiter.execute("select name from sqlite_master where name = 'a'").fetchall() == [
            ("a",)
        ]
    finally:
        release.join()
        holder.close()
        waiter.close()


@pytest.mark.parametrize("busy_timeout", [-1.0, float("nan"), float("inf")])
def test_busy_timeout_must_be_a_finite_non_negative_number(busy_timeout: float) -> None:
    connection = sqlite3.connect(":memory:", autocommit=True)
    try:
        with pytest.raises(ValueError, match="busy_timeout"):
            SQLiteMigrator(connection, FileMigrationProvider(Path(".")), busy_timeout=busy_timeout)
    finally:
        connection.close()
