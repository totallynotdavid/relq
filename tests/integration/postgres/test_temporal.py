"""PostgreSQL temporal semantics and interval codec contracts."""

import datetime
import os
from dataclasses import dataclass

import asyncpg
import pytest
from relq import (
    Interval,
    SelectQuery,
    add_interval,
    at_time_zone,
    date_bin,
    insert_into,
    interval_decoder,
    make_interval,
    make_timestamp,
    make_timestamptz,
    row_adapter,
    select,
)
from relq_postgres import PostgresDatabase

from tests.integration.postgres.support import configured_harness, users

pytestmark = pytest.mark.skipif(
    os.environ.get("RELQ_TEST_POSTGRES_DSN") is None,
    reason="set RELQ_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)


async def test_at_time_zone_preserves_dst_transition_semantics(
    database: PostgresDatabase,
) -> None:
    await database.execute(insert_into(users).values(name="dst probe"))
    query = (
        select(
            at_time_zone(make_timestamp(2024, 3, 10, 1, 30, 0.0), "America/New_York"),
            at_time_zone(make_timestamp(2024, 3, 10, 3, 30, 0.0), "America/New_York"),
        )
        .from_(users)
        .limit(1)
    )
    row = await database.fetch_one(query)
    assert row == (
        datetime.datetime(2024, 3, 10, 6, 30, tzinfo=datetime.UTC),
        datetime.datetime(2024, 3, 10, 7, 30, tzinfo=datetime.UTC),
    )


async def test_zoned_timestamp_construction_and_date_bin_use_postgres_semantics(
    database: PostgresDatabase,
) -> None:
    await database.execute(insert_into(users).values(name="temporal constructor probe"))
    row = await database.fetch_one(
        select(
            make_timestamptz(2024, 3, 10, 1, 30, 0.0, "America/New_York"),
            date_bin(
                Interval(microseconds=3_600_000_000),
                make_timestamp(2024, 3, 10, 1, 44, 17.0),
                make_timestamp(2001, 1, 1, 0, 0, 0.0),
            ),
        )
        .from_(users)
        .limit(1)
    )
    assert row == (
        datetime.datetime(2024, 3, 10, 6, 30, tzinfo=datetime.UTC),
        datetime.datetime.combine(datetime.date(2024, 3, 10), datetime.time(1, 0)),
    )


async def test_postgresql_intervals_preserve_months_separately_from_days(
    database: PostgresDatabase,
) -> None:
    await database.execute(insert_into(users).values(name="interval calendar probe"))
    base = make_timestamp(2024, 1, 31, 0, 0, 0.0)
    row = await database.fetch_one(
        select(
            add_interval(base, Interval(months=1)),
            add_interval(base, Interval(days=30)),
        )
        .from_(users)
        .limit(1)
    )
    assert row is not None
    assert (row[0].date(), row[1].date()) == (
        datetime.date(2024, 2, 29),
        datetime.date(2024, 3, 1),
    )


@dataclass(frozen=True, slots=True)
class IntervalRow:
    value: Interval


async def _assert_interval_apis(
    database: PostgresDatabase, query: SelectQuery[tuple[Interval]], expected: Interval
) -> None:
    adapter = row_adapter(IntervalRow, decoders=(interval_decoder(),))
    assert await database.fetch_all(query) == [(expected,)]
    assert await database.fetch_one(query) == (expected,)
    assert await database.fetch_all_as(query, adapter) == [IntervalRow(expected)]
    assert await database.fetch_one_as(query, adapter) == IntervalRow(expected)
    async with database.fetch_iter(query) as rows:
        assert [row async for row in rows] == [(expected,)]


async def test_interval_codec_is_configured_for_direct_and_pool_operations(
    database: PostgresDatabase, postgres_schema: str
) -> None:
    await database.execute(insert_into(users).values(name="interval codec probe"))
    query = select(make_interval(months=2, days=3, hours=4))
    query = query.from_(users).limit(1)
    expected = Interval(months=2, days=3, microseconds=14_400_000_000)
    await _assert_interval_apis(database, query, expected)

    pool = await asyncpg.create_pool(
        configured_harness().dsn,
        server_settings={"search_path": f"{postgres_schema}, public"},
    )
    try:
        pooled = PostgresDatabase(pool)
        await _assert_interval_apis(pooled, query, expected)
        async with pooled.transaction() as transaction:
            assert await transaction.fetch_one(query) == (expected,)
    finally:
        await pool.close()
