"""Executor ergonomics contracts for SQLite."""

import sqlite3
from dataclasses import dataclass

import pytest
from relq import Column, Table, column, insert_into, row_adapter, select
from relq_sqlite import NoResultError, QueryEvent, SQLiteDatabase, TransactionUnavailableError


class ErgonomicUsers(Table):
    id: Column[int] = column(int)
    name: Column[str] = column(str)


users = ErgonomicUsers("ergonomic_users")


@dataclass(frozen=True)
class UserRow:
    id: int
    name: str


def test_fetch_one_or_raise_and_fetch_one_as_or_raise() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table ergonomic_users (id integer primary key, name text not null)")
    database = SQLiteDatabase(connection)
    query = select(users.id, users.name).from_(users).where(users.id.eq(1))

    with pytest.raises(NoResultError, match="query returned no rows"):
        database.fetch_one_or_raise(query)

    class MissingUserError(LookupError):
        pass

    with pytest.raises(MissingUserError):
        database.fetch_one_or_raise(query, error=MissingUserError)
    with pytest.raises(NoResultError):
        database.fetch_one_as_or_raise(query, row_adapter(UserRow))


def test_observer_receives_successful_and_failed_operations() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table ergonomic_users (id integer primary key, name text not null)")
    events: list[QueryEvent] = []
    database = SQLiteDatabase(connection, observer=events.append)
    query = select(users.id).from_(users)

    database.execute(insert_into(users).values(id=1, name="Ada"))
    assert database.fetch_all(query) == [(1,)]
    assert database.fetch_one(query) == (1,)

    missing_users = ErgonomicUsers("missing_users")
    with pytest.raises(sqlite3.OperationalError):
        database.fetch_all(select(missing_users.id).from_(missing_users))

    assert len(events) == 4
    assert events[0].error is None
    assert events[0].row_count == 1
    assert events[1].row_count == 1
    assert events[2].row_count == 1
    assert isinstance(events[3].error, sqlite3.OperationalError)
    assert events[3].row_count is None
    assert all(event.duration >= 0 for event in events)


def test_observer_failure_does_not_replace_query_result_or_error() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table ergonomic_users (id integer primary key, name text not null)")

    def broken_observer(event: QueryEvent) -> None:
        raise RuntimeError("observer failed")

    database = SQLiteDatabase(connection, observer=broken_observer)
    assert database.execute(insert_into(users).values(id=1, name="Ada")) == 1
    assert database.fetch_all(select(users.id).from_(users)) == [(1,)]

    missing_users = ErgonomicUsers("missing_users")
    with pytest.raises(sqlite3.OperationalError):
        database.fetch_all(select(missing_users.id).from_(missing_users))


def test_observer_base_exception_is_not_suppressed() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table ergonomic_users (id integer primary key, name text not null)")

    def exiting_observer(event: QueryEvent) -> None:
        raise KeyboardInterrupt

    database = SQLiteDatabase(connection, observer=exiting_observer)
    with pytest.raises(KeyboardInterrupt):
        database.execute(insert_into(users).values(id=1, name="Ada"))
    assert connection.execute("select name from ergonomic_users").fetchone() == ("Ada",)


def test_observer_receives_transaction_control_events() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table ergonomic_users (id integer primary key, name text not null)")
    events: list[QueryEvent] = []
    database = SQLiteDatabase(connection, observer=events.append)

    transaction = database.begin()
    savepoint = transaction.savepoint("checkpoint")
    savepoint.rollback()
    savepoint.release()
    transaction.commit()

    assert [event.sql for event in events] == [
        "begin",
        'savepoint "checkpoint"',
        'rollback to savepoint "checkpoint"',
        'release savepoint "checkpoint"',
        "commit",
    ]


def test_observer_failure_during_nested_begin_preserves_parent() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table ergonomic_users (id integer primary key, name text not null)")
    events: list[QueryEvent] = []

    def observer(event: QueryEvent) -> None:
        events.append(event)
        if event.sql.startswith("savepoint"):
            raise KeyboardInterrupt

    database = SQLiteDatabase(connection, observer=observer)
    parent = database.begin()
    with pytest.raises(KeyboardInterrupt):
        parent.begin()
    parent.execute(insert_into(users).values(id=1, name="Parent survives"))
    parent.commit()

    assert database.fetch_all(select(users.name).from_(users)) == [("Parent survives",)]


def test_controlled_transaction_savepoint_and_lifecycle() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table ergonomic_users (id integer primary key, name text not null)")
    database = SQLiteDatabase(connection)

    transaction = database.begin()
    transaction.execute(insert_into(users).values(id=1, name="Ada"))
    savepoint = transaction.savepoint("after_ada")
    transaction.execute(insert_into(users).values(id=2, name="Grace"))
    savepoint.rollback()
    savepoint.release()
    transaction.commit()

    assert database.fetch_all(select(users.id).from_(users)) == [(1,)]

    with pytest.raises(RuntimeError, match="no longer usable"):
        transaction.fetch_all(query=select(users.id).from_(users))

    rolled_back = database.begin()
    rolled_back.execute(insert_into(users).values(id=3, name="Lin"))
    rolled_back.rollback()
    with pytest.raises(RuntimeError, match="no longer usable"):
        rolled_back.commit()
    assert database.fetch_all(select(users.id).from_(users)) == [(1,)]


def test_controlled_transaction_rejects_duplicate_savepoint_names() -> None:
    connection = sqlite3.connect(":memory:")
    database = SQLiteDatabase(connection)
    transaction = database.begin()

    savepoint = transaction.savepoint("same")
    with pytest.raises(ValueError, match="already active"):
        transaction.savepoint("same")
    savepoint.release()
    transaction.savepoint("same").release()
    transaction.rollback()


def test_controlled_transaction_rejects_case_insensitive_duplicate_savepoint_names() -> None:
    connection = sqlite3.connect(":memory:")
    database = SQLiteDatabase(connection)
    transaction = database.begin()

    savepoint = transaction.savepoint("same")
    with pytest.raises(ValueError, match="already active"):
        transaction.savepoint("SAME")
    savepoint.release()
    transaction.rollback()


def test_controlled_transaction_savepoint_names_use_ascii_case_rules() -> None:
    connection = sqlite3.connect(":memory:")
    database = SQLiteDatabase(connection)
    transaction = database.begin()

    non_ascii = transaction.savepoint("ß")
    ascii_distinct = transaction.savepoint("ss")
    ascii_distinct.release()
    non_ascii.release()
    transaction.rollback()


def test_observer_failure_during_nested_rollback_preserves_parent() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table ergonomic_users (id integer primary key, name text not null)")

    def observer(event: QueryEvent) -> None:
        if event.sql.startswith("rollback to"):
            raise KeyboardInterrupt

    database = SQLiteDatabase(connection, observer=observer)
    parent = database.begin()
    child = parent.begin()
    with pytest.raises(KeyboardInterrupt):
        child.rollback()

    parent.execute(insert_into(users).values(id=1, name="Parent survives"))
    parent.commit()
    assert database.fetch_all(select(users.name).from_(users)) == [("Parent survives",)]


@pytest.mark.parametrize("operation", ("release", "rollback"))
def test_failed_savepoint_operation_recovers_transaction(operation: str) -> None:
    connection = sqlite3.connect(":memory:")
    events: list[QueryEvent] = []
    database = SQLiteDatabase(connection, observer=events.append)
    transaction = database.begin()
    savepoint = transaction.savepoint("doomed")

    connection.execute('release savepoint "doomed"')
    with pytest.raises(sqlite3.OperationalError, match="no such savepoint"):
        if operation == "release":
            savepoint.release()
        else:
            savepoint.rollback()

    with pytest.raises(TransactionUnavailableError, match="no longer usable"):
        savepoint.release()
    with pytest.raises(TransactionUnavailableError, match="no longer usable"):
        transaction.execute(insert_into(users).values(id=1, name="invalid"))
    assert events[-1].sql == "rollback"
    assert events[-1].error is None
    assert events[-2].error is not None
    fresh = database.begin()
    replacement = fresh.savepoint("doomed")
    replacement.release()
    fresh.rollback()


def test_database_wrappers_share_sqlite_transaction_state() -> None:
    connection = sqlite3.connect(":memory:")
    first_database = SQLiteDatabase(connection)
    second_database = SQLiteDatabase(connection)

    first = first_database.begin()
    second = second_database.begin()
    first.commit()

    with pytest.raises(TransactionUnavailableError):
        second.commit()
    fresh = second_database.begin()
    fresh.rollback()


def test_savepoint_rollback_invalidates_descendant_savepoints() -> None:
    connection = sqlite3.connect(":memory:")
    database = SQLiteDatabase(connection)
    transaction = database.begin()

    first = transaction.savepoint("first")
    second = transaction.savepoint("second")
    first.rollback()

    with pytest.raises(RuntimeError, match="no longer usable"):
        second.release()
    replacement = transaction.savepoint("second")
    replacement.release()
    first.release()
    transaction.rollback()


def test_savepoint_release_invalidates_descendant_boundaries() -> None:
    connection = sqlite3.connect(":memory:")
    database = SQLiteDatabase(connection)
    transaction = database.begin()

    first = transaction.savepoint("first")
    child = transaction.begin()
    second = transaction.savepoint("second")
    first.release()

    with pytest.raises(RuntimeError, match="no longer usable"):
        child.commit()
    with pytest.raises(RuntimeError, match="no longer usable"):
        second.release()
    replacement = transaction.savepoint("second")
    replacement.release()
    transaction.rollback()


def test_parent_completion_invalidates_open_child_transactions() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table ergonomic_users (id integer primary key, name text not null)")
    database = SQLiteDatabase(connection)

    first = database.begin()
    first.execute(insert_into(users).values(id=1, name="Ada"))
    second = database.begin()
    second.execute(insert_into(users).values(id=2, name="Grace"))

    first.commit()
    with pytest.raises(RuntimeError, match="no longer usable"):
        second.fetch_all(select(users.id).from_(users))
    with pytest.raises(RuntimeError, match="no longer usable"):
        second.commit()
    assert database.fetch_all(select(users.id).from_(users).order_by(users.id.asc())) == [
        (1,),
        (2,),
    ]

    with pytest.raises(RuntimeError, match="no longer usable"):
        first.begin()

    rolled_back = database.begin()
    rolled_back.rollback()
    with pytest.raises(RuntimeError, match="no longer usable"):
        rolled_back.begin()

    parent = database.begin()
    child = parent.begin()
    parent.rollback()
    with pytest.raises(RuntimeError, match="no longer usable"):
        child.execute(insert_into(users).values(id=4, name="Sam"))


def test_transaction_context_tracks_nested_controlled_handles() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table ergonomic_users (id integer primary key, name text not null)")
    database = SQLiteDatabase(connection)

    with database.transaction() as outer:
        child = outer.begin()
        child.execute(insert_into(users).values(id=5, name="Jo"))

    with pytest.raises(RuntimeError, match="no longer usable"):
        child.execute(insert_into(users).values(id=6, name="Invalid"))
    assert database.fetch_all(select(users.id).from_(users)) == [(5,)]


def test_failed_commit_cleans_up_the_controlled_transaction() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("pragma foreign_keys = on")
    connection.execute("create table parent (id integer primary key)")
    connection.execute(
        "create table child (id integer references parent(id) deferrable initially deferred)"
    )
    database = SQLiteDatabase(connection)

    transaction = database.begin()
    connection.execute("insert into child values (1)")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        transaction.commit()

    assert not connection.in_transaction
    next_transaction = database.begin()
    next_transaction.rollback()


def test_controlled_transaction_closes_explicit_transactions_in_autocommit_mode() -> None:
    connection = sqlite3.connect(":memory:", autocommit=True)
    connection.execute("create table ergonomic_users (id integer primary key, name text not null)")
    database = SQLiteDatabase(connection)

    committed = database.begin()
    committed.execute(insert_into(users).values(id=1, name="Committed"))
    committed.commit()
    assert not connection.in_transaction

    rolled_back = database.begin()
    rolled_back.execute(insert_into(users).values(id=2, name="Rolled back"))
    rolled_back.rollback()
    assert not connection.in_transaction
    assert database.fetch_all(select(users.id).from_(users)) == [(1,)]


def test_begin_adopts_existing_implicit_transaction_and_honors_isolation_level() -> None:
    connection = sqlite3.connect(":memory:", isolation_level="IMMEDIATE")
    connection.execute("create table ergonomic_users (id integer primary key, name text not null)")
    events: list[QueryEvent] = []
    database = SQLiteDatabase(connection, observer=events.append)

    database.execute(insert_into(users).values(id=1, name="Ada"))
    events.clear()
    with database.transaction():
        database.execute(insert_into(users).values(id=2, name="Grace"))

    assert not connection.in_transaction
    assert [event.sql for event in events] == [
        'insert into "ergonomic_users" ("id", "name") values (?, ?)',
        "commit",
    ]
    connection.rollback()
    assert database.fetch_all(select(users.id).from_(users).order_by(users.id.asc())) == [
        (1,),
        (2,),
    ]

    fresh_connection = sqlite3.connect(":memory:", isolation_level="IMMEDIATE")
    fresh_connection.execute(
        "create table ergonomic_users (id integer primary key, name text not null)"
    )
    fresh_statements: list[str] = []
    fresh_connection.set_trace_callback(fresh_statements.append)
    fresh_database = SQLiteDatabase(fresh_connection)
    transaction = fresh_database.begin()
    transaction.rollback()
    assert any("BEGIN IMMEDIATE" in statement.upper() for statement in fresh_statements)
