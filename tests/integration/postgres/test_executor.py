"""Real PostgreSQL execution contracts for queries and mutations."""

import datetime
import os

import asyncpg
import pytest
from asyncpg import Connection
from relq import (
    cte,
    delete_from,
    excluded,
    insert_into,
    now,
    scalar,
    select,
    subtract_interval,
    update,
)
from relq_postgres import PostgresDatabase

from tests.integration.postgres.matrix_fixture import prepare_postgres_matrix
from tests.integration.postgres.support import Active, archive, configured_harness, users
from tests.relational_matrix import assert_relational_matrix

pytestmark = pytest.mark.skipif(
    os.environ.get("RELQ_TEST_POSTGRES_DSN") is None,
    reason="set RELQ_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)


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
        .for_update(of=users, skip_locked=True)
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
        select(subtract_interval(now(), datetime.timedelta(days=1))).from_(users).limit(1)
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
