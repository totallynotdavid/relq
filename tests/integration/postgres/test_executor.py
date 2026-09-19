"""Real PostgreSQL execution contracts for queries and mutations."""

import asyncio
import datetime
import os
from dataclasses import dataclass
from typing import Protocol, cast

import asyncpg
import pytest
from asyncpg import Connection
from relq import (
    Column,
    Interval,
    Table,
    column,
    cte,
    delete_from,
    excluded,
    insert_into,
    row_adapter,
    scalar,
    select,
    subtract_interval,
    transaction_timestamp,
    update,
)
from relq_postgres import NoResultError, PostgresDatabase, QueryEvent, TransactionUnavailableError

from tests.integration.postgres.matrix_fixture import prepare_postgres_matrix
from tests.integration.postgres.support import Active, archive, configured_harness, users
from tests.relational_matrix import assert_relational_matrix

pytestmark = pytest.mark.skipif(
    os.environ.get("RELQ_TEST_POSTGRES_DSN") is None,
    reason="set RELQ_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)


@dataclass(frozen=True)
class UserRow:
    id: int
    name: str


class _TransactionInternals(Protocol):
    _id: str | None


class _ControlledTransactionInternals(Protocol):
    _transaction: _TransactionInternals


async def test_executor_supports_ctes_returning_and_conflicts(database: PostgresDatabase) -> None:
    assert await database.fetch_all(
        insert_into(users).values(name="Ada").returning(users.id, users.name)
    ) == [(1, "Ada")]
    assert await database.fetch_all(
        update(users).values(name="Grace").where(users.id.eq(1)).returning(users.name)
    ) == [("Grace",)]
    active = cte(Active, "active")
    assert await database.fetch_all(
        select(active.id).from_(active).with_(active, select(users.id).from_(users))
    ) == [(1,)]
    copied = insert_into(archive).from_select(
        select(users.id, users.name).from_(users), archive.id, archive.name
    )
    assert await database.execute(copied) == 1
    assert await database.fetch_all(
        insert_into(users)
        .values(name="Grace")
        .on_conflict(users.name)
        .do_update(name=excluded(users.name))
        .returning(users.id)
    ) == [(1,)]
    assert await database.fetch_all(
        delete_from(users).where(users.id.eq(1)).returning(users.id)
    ) == [(1,)]


async def test_aliases_correlated_queries_nulls_and_transaction_rollback(
    database: PostgresDatabase,
) -> None:
    await database.execute(insert_into(users).values(name="Ada", manager_id=None, active=True))
    await database.execute(insert_into(users).values(name="Grace", manager_id=1, active=False))
    manager = users.as_("manager")
    aliases = (
        select(users.name, manager.name)
        .from_(users)
        .inner_join(manager, on=users.manager_id.eq(manager.id))
    )
    assert await database.fetch_all(aliases) == [("Grace", "Ada")]
    correlated = select(manager.name).from_(manager).where(manager.id.eq(users.manager_id))
    assert await database.fetch_all(
        select(users.name, scalar(correlated)).from_(users).order_by(users.id.asc())
    ) == [("Ada", None), ("Grace", "Ada")]
    assert await database.fetch_all(
        select(users.name).from_(users).where(users.manager_id.eq(None))
    ) == [("Ada",)]
    with pytest.raises(RuntimeError, match="rollback"):
        async with database.transaction() as transaction:
            await transaction.execute(insert_into(archive).values(id=99, name="temporary"))
            raise RuntimeError("rollback")
    assert await database.fetch_all(select(archive.id).from_(archive)) == []


async def test_pool_transaction_binds_every_operation_to_one_acquired_connection(
    postgres_schema: str, database: PostgresDatabase
) -> None:
    pool = await asyncpg.create_pool(
        configured_harness().dsn, server_settings={"search_path": f"{postgres_schema}, public"}
    )
    try:
        database = PostgresDatabase(pool)
        async with database.transaction() as transaction:
            assert (
                await transaction.execute(insert_into(users).values(name="Ada", manager_id=None))
                == 1
            )
            assert await transaction.fetch_all(select(users.name).from_(users)) == [("Ada",)]
        assert await database.fetch_all(select(users.name).from_(users)) == [("Ada",)]
    finally:
        await pool.close()


async def test_pool_transactions_have_independent_connection_state(
    postgres_schema: str, database: PostgresDatabase
) -> None:
    pool = await asyncpg.create_pool(
        configured_harness().dsn,
        min_size=4,
        max_size=4,
        server_settings={"search_path": f"{postgres_schema}, public"},
    )
    try:
        pooled = PostgresDatabase(pool)

        async def insert(name: str) -> None:
            async with pooled.transaction() as transaction:
                await transaction.execute(insert_into(users).values(name=name, manager_id=None))
                await asyncio.sleep(0)

        await asyncio.gather(*(insert(f"Pool {index}") for index in range(4)))
        rows = await pooled.fetch_all(select(users.name).from_(users))
        assert sorted(rows) == [(f"Pool {index}",) for index in range(4)]
    finally:
        await pool.close()


async def test_for_update_skip_locked_and_timestamp_duration_execute(
    database: PostgresDatabase, postgres_schema: str
) -> None:
    await database.execute(insert_into(users).values(name="Ada", manager_id=None))
    await database.execute(insert_into(users).values(name="Grace", manager_id=None))
    claim = (
        select(users.id)
        .from_(users)
        .order_by(users.id.asc())
        .limit(1)
        .for_update(users)
        .skip_locked()
    )
    async with database.transaction() as first_worker:
        assert await first_worker.fetch_all(claim) == [(1,)]
        connection = await asyncpg.connect(
            configured_harness().dsn,
            server_settings={"search_path": f"{postgres_schema}, public"},
        )
        try:
            second_worker = PostgresDatabase(connection)
            async with second_worker.transaction() as transaction:
                assert await transaction.fetch_all(claim) == [(2,)]
        finally:
            await connection.close()

    rows = await database.fetch_all(
        select(subtract_interval(transaction_timestamp(), Interval(days=1))).from_(users).limit(1)
    )
    assert len(rows) == 1
    assert isinstance(rows[0][0], datetime.datetime)


async def test_relational_matrix(database: PostgresDatabase, postgres_admin: Connection) -> None:
    await prepare_postgres_matrix(postgres_admin)
    await assert_relational_matrix(database)


async def test_fetch_iter_streams_rows_through_a_cursor(database: PostgresDatabase) -> None:
    await database.execute(insert_into(users).values(name="Ada", manager_id=None))
    await database.execute(insert_into(users).values(name="Grace", manager_id=None))
    async with database.fetch_iter(
        select(users.name).from_(users).order_by(users.id.asc())
    ) as rows:
        assert [row async for row in rows] == [("Ada",), ("Grace",)]


async def test_fetch_iter_does_not_attribute_consumer_errors_to_the_query(
    postgres_admin: Connection,
    database: PostgresDatabase,
) -> None:
    await postgres_admin.execute(
        "insert into relq_integration_users (name, manager_id) values ($1, $2)",
        "Streamed",
        None,
    )
    events: list[QueryEvent] = []
    observed = PostgresDatabase(postgres_admin, observer=events.append)

    with pytest.raises(RuntimeError, match="consumer failed"):
        async with observed.fetch_iter(select(users.name).from_(users)) as rows:
            assert await anext(rows) == ("Streamed",)
            raise RuntimeError("consumer failed")

    assert events[-1].row_count == 1
    assert events[-1].error is None


async def test_fetch_iter_on_pool_binds_to_one_acquired_connection(
    postgres_schema: str, database: PostgresDatabase
) -> None:
    await database.execute(insert_into(users).values(name="Ada", manager_id=None))
    pool = await asyncpg.create_pool(
        configured_harness().dsn, server_settings={"search_path": f"{postgres_schema}, public"}
    )
    try:
        pooled = PostgresDatabase(pool)
        async with pooled.fetch_iter(select(users.name).from_(users)) as rows:
            assert [row async for row in rows] == [("Ada",)]
    finally:
        await pool.close()


async def test_fetch_one_or_raise_and_observer_cover_postgres_operations(
    postgres_admin: Connection, database: PostgresDatabase
) -> None:
    await database.execute(insert_into(users).values(name="Ada", manager_id=None))
    events: list[QueryEvent] = []
    observed = PostgresDatabase(postgres_admin, observer=events.append)
    query = select(users.id, users.name).from_(users).where(users.name.eq("missing"))

    with pytest.raises(NoResultError, match="query returned no rows"):
        await observed.fetch_one_or_raise(query)
    with pytest.raises(LookupError, match="domain missing"):
        await observed.fetch_one_or_raise(
            query,
            error=lambda: LookupError("domain missing"),
        )
    events_before_mapped_fetch = len(events)
    with pytest.raises(LookupError, match="domain missing"):
        await observed.fetch_one_as_or_raise(
            query,
            row_adapter(UserRow),
            error=lambda: LookupError("domain missing"),
        )
    assert len(events) == events_before_mapped_fetch + 1

    assert await observed.fetch_all(select(users.id).from_(users)) == [(1,)]
    async with observed.fetch_iter(select(users.id).from_(users)) as rows:
        assert [row async for row in rows] == [(1,)]

    class MissingUsers(Table):
        id: Column[int] = column(int)

    missing_users = MissingUsers("missing_users")
    with pytest.raises(asyncpg.UndefinedTableError):
        await observed.fetch_all(select(missing_users.id).from_(missing_users))

    # Each or_raise wrapper delegates to one fetch event. The stream also emits
    # its begin and commit boundary events, in addition to the stream event.
    assert len(events) == 8
    assert events[0].row_count == 0
    assert events[1].row_count == 0
    assert events[2].row_count == 0
    assert events[3].row_count == 1
    assert events[4].sql == "begin"
    assert events[4].row_count == 0
    assert events[5].sql == "commit"
    assert events[5].row_count == 0
    assert events[6].row_count == 1
    assert events[7].row_count is None
    assert isinstance(events[7].error, asyncpg.UndefinedTableError)


async def test_observer_failure_does_not_replace_query_result_or_error(
    postgres_admin: Connection,
    database: PostgresDatabase,
) -> None:
    def broken_observer(event: QueryEvent) -> None:
        raise RuntimeError("observer failed")

    observed = PostgresDatabase(postgres_admin, observer=broken_observer)
    assert await observed.execute(insert_into(users).values(name="Observed", manager_id=None)) == 1
    assert await observed.fetch_all(select(users.name).from_(users)) == [("Observed",)]

    class MissingUsers(Table):
        id: Column[int] = column(int)

    missing_users = MissingUsers("missing_users")
    with pytest.raises(asyncpg.UndefinedTableError):
        await observed.fetch_all(select(missing_users.id).from_(missing_users))


async def test_observer_base_exception_is_not_suppressed(
    postgres_admin: Connection,
    database: PostgresDatabase,
) -> None:
    def exiting_observer(event: QueryEvent) -> None:
        raise KeyboardInterrupt

    observed = PostgresDatabase(postgres_admin, observer=exiting_observer)
    with pytest.raises(KeyboardInterrupt):
        await observed.execute(insert_into(users).values(name="Observed", manager_id=None))
    assert (
        await postgres_admin.fetchval(
            "select name from relq_integration_users where name = 'Observed'"
        )
        == "Observed"
    )


async def test_observer_failure_during_nested_begin_preserves_parent(
    postgres_admin: Connection,
) -> None:
    events: list[QueryEvent] = []

    def observer(event: QueryEvent) -> None:
        events.append(event)
        if event.sql.startswith("savepoint"):
            raise KeyboardInterrupt

    database = PostgresDatabase(postgres_admin, observer=observer)
    parent = await database.begin()
    with pytest.raises(KeyboardInterrupt):
        await parent.begin()
    await parent.commit()


async def test_controlled_transaction_savepoint_lifecycle(database: PostgresDatabase) -> None:
    transaction = await database.begin()
    await transaction.execute(insert_into(users).values(name="Ada", manager_id=None))
    savepoint = await transaction.savepoint("after_ada")
    await transaction.execute(insert_into(users).values(name="Grace", manager_id=None))
    await savepoint.rollback()
    await savepoint.release()
    await transaction.commit()

    assert await database.fetch_all(select(users.name).from_(users)) == [("Ada",)]
    with pytest.raises(RuntimeError, match="no longer usable"):
        await transaction.fetch_all(select(users.name).from_(users))


async def test_controlled_transaction_savepoint_registry_rejects_stale_handles(
    database: PostgresDatabase,
) -> None:
    duplicate_names = await database.begin()
    savepoint = await duplicate_names.savepoint("same")
    with pytest.raises(ValueError, match="already active"):
        await duplicate_names.savepoint("same")
    with pytest.raises(ValueError, match="already active"):
        await duplicate_names.savepoint("SAME")
    await savepoint.release()
    await (await duplicate_names.savepoint("same")).release()
    await duplicate_names.rollback()

    unicode_names = await database.begin()
    non_ascii = await unicode_names.savepoint("ß")
    ascii_distinct = await unicode_names.savepoint("ss")
    await ascii_distinct.release()
    await non_ascii.release()
    await unicode_names.rollback()

    descendants = await database.begin()
    first = await descendants.savepoint("first")
    second = await descendants.savepoint("second")
    await first.rollback()
    with pytest.raises(RuntimeError, match="no longer usable"):
        await second.release()
    replacement = await descendants.savepoint("second")
    await replacement.release()
    await first.release()
    await descendants.rollback()

    release_descendants = await database.begin()
    first = await release_descendants.savepoint("first")
    child = await release_descendants.begin()
    second = await release_descendants.savepoint("second")
    await first.release()
    with pytest.raises(RuntimeError, match="no longer usable"):
        await child.commit()
    with pytest.raises(RuntimeError, match="no longer usable"):
        await second.release()
    replacement = await release_descendants.savepoint("second")
    await replacement.release()
    await release_descendants.rollback()

    long_name = "x" * 64
    names = await database.begin()
    with pytest.raises(ValueError, match="63 UTF-8 bytes"):
        await names.savepoint(long_name)
    await names.rollback()


async def test_controlled_transaction_parent_completion_invalidates_children(
    database: PostgresDatabase,
) -> None:
    parent = await database.begin()
    child = await parent.begin()
    await parent.commit()
    with pytest.raises(RuntimeError, match="no longer usable"):
        await child.fetch_all(select(users.name).from_(users))
    with pytest.raises(RuntimeError, match="no longer usable"):
        await child.commit()

    rolled_back_parent = await database.begin()
    rolled_back_child = await rolled_back_parent.begin()
    await rolled_back_parent.rollback()
    with pytest.raises(RuntimeError, match="no longer usable"):
        await rolled_back_child.execute(insert_into(users).values(name="invalid"))


async def test_transaction_context_tracks_nested_controlled_handles(
    database: PostgresDatabase,
) -> None:
    async with database.transaction() as outer:
        context_child = await outer.begin()
        await context_child.execute(insert_into(users).values(name="Context", manager_id=None))
    with pytest.raises(RuntimeError, match="no longer usable"):
        await context_child.execute(insert_into(users).values(name="Invalid", manager_id=None))
    assert await database.fetch_all(select(users.name).from_(users)) == [("Context",)]

    rolled_back = await database.begin()
    await rolled_back.execute(insert_into(users).values(name="Lin", manager_id=None))
    await rolled_back.rollback()
    with pytest.raises(RuntimeError, match="no longer usable"):
        await rolled_back.commit()


async def test_transaction_connection_commits_raw_work(database: PostgresDatabase) -> None:
    async with database.transaction_connection() as connection:
        await connection.execute(
            "insert into relq_integration_users (name, manager_id) values ($1, $2)",
            "Queue-visible",
            None,
        )
    assert await database.fetch_all(select(users.name).from_(users)) == [
        ("Queue-visible",),
    ]


async def test_database_wrappers_share_postgres_transaction_state(
    postgres_admin: Connection,
) -> None:
    first_database = PostgresDatabase(postgres_admin)
    second_database = PostgresDatabase(postgres_admin)

    first = await first_database.begin()
    second = await second_database.begin()
    await first.commit()

    with pytest.raises(TransactionUnavailableError):
        await second.commit()
    fresh = await second_database.begin()
    await fresh.rollback()


async def test_begin_does_not_recover_a_caller_managed_transaction(
    postgres_admin: Connection,
    database: PostgresDatabase,
) -> None:
    await postgres_admin.execute("begin")
    await postgres_admin.execute(
        "insert into relq_integration_users (name, manager_id) values ($1, $2)",
        "Manual before failed begin",
        None,
    )

    with pytest.raises(asyncpg.InterfaceError, match="manually started"):
        await database.begin()

    await postgres_admin.execute(
        "insert into relq_integration_users (name, manager_id) values ($1, $2)",
        "Manual after failed begin",
        None,
    )
    await postgres_admin.execute("commit")
    assert await database.fetch_all(select(users.name).from_(users).order_by(users.id.asc())) == [
        ("Manual before failed begin",),
        ("Manual after failed begin",),
    ]


async def test_nested_transaction_rollback_releases_its_savepoint(
    postgres_admin: Connection,
) -> None:
    events: list[QueryEvent] = []
    database = PostgresDatabase(postgres_admin, observer=events.append)
    parent = await database.begin()
    child = await parent.begin()
    await child.rollback()
    await parent.rollback()

    assert len(events) == 5
    assert events[0].sql == "begin"
    assert events[1].sql.startswith("savepoint ")
    assert events[2].sql.startswith("rollback to ")
    assert events[3].sql == f"release savepoint {events[1].sql.removeprefix('savepoint ')}"
    assert events[4].sql == "rollback"


@pytest.mark.parametrize("operation", ("release", "rollback"))
async def test_failed_savepoint_operation_recovers_transaction(
    postgres_admin: Connection, database: PostgresDatabase, operation: str
) -> None:
    events: list[QueryEvent] = []
    observed_database = PostgresDatabase(postgres_admin, observer=events.append)
    transaction = await observed_database.begin()
    savepoint = await transaction.savepoint("doomed")

    await postgres_admin.execute('release savepoint "doomed"')
    with pytest.raises(asyncpg.InvalidSavepointSpecificationError):
        if operation == "release":
            await savepoint.release()
        else:
            await savepoint.rollback()

    with pytest.raises(TransactionUnavailableError, match="no longer usable"):
        await savepoint.release()
    with pytest.raises(TransactionUnavailableError, match="no longer usable"):
        await transaction.execute(insert_into(users).values(name="invalid", manager_id=None))
    assert events[-1].sql == "rollback"
    assert events[-1].error is None
    assert events[-2].error is not None
    fresh = await database.begin()
    replacement = await fresh.savepoint("doomed")
    await replacement.release()
    await fresh.rollback()


async def test_failed_nested_savepoint_preserves_application_transaction(
    postgres_admin: Connection, database: PostgresDatabase
) -> None:
    events: list[QueryEvent] = []
    async with postgres_admin.transaction():
        await postgres_admin.execute(
            "insert into relq_integration_users (name, manager_id) values ($1, $2)",
            "Application before",
            None,
        )
        relq_database = PostgresDatabase(postgres_admin, observer=events.append)
        transaction = await relq_database.begin()
        savepoint = await transaction.savepoint("doomed")
        await postgres_admin.execute('release savepoint "doomed"')

        with pytest.raises(asyncpg.InvalidSavepointSpecificationError):
            await savepoint.release()
        with pytest.raises(TransactionUnavailableError, match="no longer usable"):
            await transaction.execute(insert_into(users).values(name="invalid", manager_id=None))

        await postgres_admin.execute(
            "insert into relq_integration_users (name, manager_id) values ($1, $2)",
            "Application after",
            None,
        )

    assert any(event.sql.startswith("rollback to ") and event.error is None for event in events)
    assert any(
        event.sql.startswith("release savepoint ") and event.error is None for event in events
    )
    assert await database.fetch_all(select(users.name).from_(users).order_by(users.id.asc())) == [
        ("Application before",),
        ("Application after",),
    ]


async def test_failed_nested_begin_recovers_and_invalidates_parent(
    postgres_admin: Connection, database: PostgresDatabase
) -> None:
    parent = await database.begin()
    with pytest.raises(asyncpg.UndefinedTableError):
        await postgres_admin.execute("select * from relq_missing_table")

    with pytest.raises(asyncpg.InFailedSQLTransactionError):
        await parent.begin()

    with pytest.raises(TransactionUnavailableError, match="no longer usable"):
        await parent.execute(insert_into(users).values(name="invalid", manager_id=None))
    fresh = await database.begin()
    await fresh.rollback()


async def test_direct_begin_nesting_invalidates_later_handles(
    database: PostgresDatabase,
) -> None:
    first = await database.begin()
    second = await database.begin()
    await first.commit()
    with pytest.raises(RuntimeError, match="no longer usable"):
        await second.commit()

    parent = await database.begin()
    first_child = await parent.begin()
    second_child = await parent.begin()
    await first_child.commit()
    with pytest.raises(RuntimeError, match="no longer usable"):
        await second_child.commit()


async def test_explicit_savepoint_rejects_asyncpg_generated_name(
    database: PostgresDatabase,
) -> None:
    parent = await database.begin()
    nested = await parent.begin()
    generated_name = cast(_ControlledTransactionInternals, nested)._transaction._id  # pyright: ignore[reportPrivateUsage]
    assert isinstance(generated_name, str)
    with pytest.raises(ValueError, match="already active"):
        await nested.savepoint(generated_name)
    await parent.rollback()


async def test_failed_commit_recovers_direct_connection(
    postgres_admin: Connection,
    database: PostgresDatabase,
) -> None:
    await postgres_admin.execute("create table deferred_parent (id integer primary key)")
    await postgres_admin.execute(
        "create table deferred_child (id integer references deferred_parent(id) "
        "deferrable initially deferred)"
    )

    transaction = await database.begin()
    await postgres_admin.execute("insert into deferred_child values (1)")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await transaction.commit()

    await database.execute(insert_into(users).values(name="Recovered", manager_id=None))
    recovered = await database.begin()
    await recovered.rollback()


async def test_failed_commit_invalidates_handles_sharing_connection(
    postgres_admin: Connection,
    database: PostgresDatabase,
) -> None:
    await postgres_admin.execute("create table shared_deferred_parent (id integer primary key)")
    await postgres_admin.execute(
        "create table shared_deferred_child (id integer references shared_deferred_parent(id) "
        "deferrable initially deferred)"
    )

    outer = await database.begin()
    other_database = PostgresDatabase(outer.raw_connection)
    other = await other_database.begin()
    await postgres_admin.execute("insert into shared_deferred_child values (1)")

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await outer.commit()
    with pytest.raises(TransactionUnavailableError):
        await other.execute(insert_into(users).values(name="invalid", manager_id=None))
