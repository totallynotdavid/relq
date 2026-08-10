"""PostgreSQL execution half of portable analytic behavior contracts."""

import os

import pytest
from relq import (
    RowDecodingError,
    WindowExclusion,
    count,
    cume_dist,
    current_row,
    enum_decoder,
    insert_into,
    json_decoder,
    list_decoder,
    percent_rank,
    rank,
    row_adapter,
    row_number,
    select,
    select_model,
    sum,
    unbounded_preceding,
    uuid_decoder,
)
from relq_postgres import PostgresDatabase

from tests.integration.postgres.support import DecodedState, UnexpectedState, users

pytestmark = pytest.mark.skipif(
    os.environ.get("RELQ_TEST_POSTGRES_DSN") is None,
    reason="set RELQ_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)


async def test_window_functions(database: PostgresDatabase) -> None:
    for name, manager_id in (("Ada", 1), ("Grace", 1), ("Lin", 2)):
        await database.execute(insert_into(users).values(name=name, manager_id=manager_id))
    query = (
        select(
            users.name,
            row_number().over().partition_by(users.manager_id).order_by(users.id.desc()),
            rank().over().partition_by(users.manager_id).order_by(users.id.desc()),
        )
        .from_(users)
        .order_by(users.name.asc())
    )
    assert await database.fetch_all(query) == [("Ada", 2, 2), ("Grace", 1, 1), ("Lin", 1, 1)]


async def test_null_ordering_and_distribution_match_the_sqlite_grid(
    database: PostgresDatabase,
) -> None:
    for name, manager_id in (("Ada", None), ("Grace", 10), ("Lin", 10), ("Mina", 20), ("Sol", 7)):
        await database.execute(insert_into(users).values(name=name, manager_id=manager_id))
    placements = (
        (users.manager_id.asc().nulls_first(), [1, 5, 2, 3, 4]),
        (users.manager_id.asc().nulls_last(), [5, 2, 3, 4, 1]),
        (users.manager_id.desc().nulls_first(), [1, 4, 2, 3, 5]),
        (users.manager_id.desc().nulls_last(), [4, 2, 3, 5, 1]),
    )
    for order, ids in placements:
        assert await database.fetch_all(select(users.id).from_(users).order_by(order)) == [
            (identifier,) for identifier in ids
        ]
    query = (
        select(
            users.id,
            percent_rank().over().order_by(users.manager_id.asc().nulls_last()),
            cume_dist().over().order_by(users.manager_id.asc().nulls_last()),
        )
        .from_(users)
        .order_by(users.id.asc())
    )
    assert await database.fetch_all(query) == [
        (1, 1.0, 1.0),
        (2, 0.25, 0.6),
        (3, 0.25, 0.6),
        (4, 0.75, 0.8),
        (5, 0.0, 0.2),
    ]


async def test_aggregate_filter_matches_sqlite_behavior_grid(database: PostgresDatabase) -> None:
    for name, active in (("Ada", True), ("Grace", False), ("Lin", True)):
        await database.execute(insert_into(users).values(name=name, active=active))
    query = (
        select(
            count().filter(users.active.is_true()),
            sum(users.id).filter(users.active.is_true()),
            count().filter(users.name.like("%a%")).filter(users.active.is_true()),
        )
        .from_(users)
        .where(users.id.gt(0))
    )
    assert await database.fetch_all(query) == [(2, 4, 1)]


async def test_window_exclusions_match_the_sqlite_peer_behavior_grid(
    database: PostgresDatabase,
) -> None:
    for name, manager_id in (("Ada", 10), ("Grace", 10), ("Lin", 20), ("Mina", 20), ("Sol", 30)):
        await database.execute(insert_into(users).values(name=name, manager_id=manager_id))
    base = (
        sum(users.id)
        .over()
        .order_by(users.manager_id.asc())
        .groups_between(unbounded_preceding(), current_row())
    )
    query = (
        select(
            users.id,
            base.exclude(WindowExclusion.NO_OTHERS),
            base.exclude(WindowExclusion.CURRENT_ROW),
            base.exclude(WindowExclusion.GROUP),
            base.exclude(WindowExclusion.TIES),
        )
        .from_(users)
        .order_by(users.id.asc())
    )
    assert await database.fetch_all(query) == [
        (1, 3, 2, None, 1),
        (2, 3, 1, None, 2),
        (3, 10, 7, 3, 6),
        (4, 10, 6, 3, 7),
        (5, 15, 10, 10, 15),
    ]


async def test_decoder_failures_have_the_same_precise_context(database: PostgresDatabase) -> None:
    await database.execute(insert_into(users).values(name="Ada"))
    cases = (
        (
            row_adapter(DecodedState, decoders=(enum_decoder(UnexpectedState),)),
            users.state,
            r"DecodedState.state at result column 0: expected UnexpectedState, received 'new' \(str\)",
        ),
        (
            row_adapter(DecodedState, decoders=(uuid_decoder(),)),
            users.name,
            r"DecodedState.state at result column 0: expected UUID, received 'Ada' \(str\)",
        ),
        (
            row_adapter(DecodedState, decoders=(json_decoder(),)),
            users.name,
            r"DecodedState.state at result column 0: expected JSON, received 'Ada' \(str\)",
        ),
        (
            row_adapter(DecodedState, decoders=(uuid_decoder(),)),
            users.manager_id,
            r"DecodedState.state at result column 0: expected UUID, received None \(NoneType\)",
        ),
        (
            row_adapter(DecodedState, decoders=(list_decoder(enum_decoder(UnexpectedState)),)),
            users.state_history,
            r"DecodedState.state at result column 0: expected list\[UnexpectedState\], received \['new'\] \(list\)",
        ),
    )
    for adapter, expression, message in cases:
        with pytest.raises(RowDecodingError, match=message):
            await database.fetch_all(select_model(adapter, expression).from_(users))
