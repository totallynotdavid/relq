"""Real PostgreSQL contracts for both ``ON CONFLICT`` predicates.

Each test reproduces one of the two hand-written statements this feature
exists for, then asserts on stored row state -- the generated SQL alone
cannot show that PostgreSQL honoured either predicate.
"""

import datetime
import os

import asyncpg
import pytest
import pytest_asyncio
from relq import excluded, insert_into, select, transaction_timestamp, update
from relq._compiler import compile_postgres, compile_sqlite
from relq_postgres import PostgresDatabase

from tests.integration.postgres.support import jobs, slots

pytestmark = pytest.mark.skipif(
    os.environ.get("RELQ_TEST_POSTGRES_DSN") is None,
    reason="set RELQ_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)

DEDUPE_STATES = ("pending", "leased")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@pytest_asyncio.fixture
async def conflict_database(postgres_admin: asyncpg.Connection) -> PostgresDatabase:
    await postgres_admin.execute("""create table relq_integration_jobs (
        id integer generated always as identity primary key,
        queue text not null,
        dedupe_key text,
        state text not null,
        updated_at integer not null default 0
    )""")
    await postgres_admin.execute(
        "create unique index relq_integration_jobs_dedupe on relq_integration_jobs "
        "(queue, dedupe_key) where dedupe_key is not null and state in ('pending', 'leased')"
    )
    await postgres_admin.execute("""create table relq_integration_slots (
        key text primary key,
        job_id integer not null,
        lease_token text not null,
        worker_id text not null,
        acquired_at timestamp with time zone not null,
        leased_until timestamp with time zone not null
    )""")
    return PostgresDatabase(postgres_admin)


def _dedupe_insert(state: str):
    """rqueue's dedupe insert: an arbiter predicate over a partial unique index."""
    return (
        insert_into(jobs)
        .values(queue="emails", dedupe_key="welcome:7", state=state)
        .on_conflict(jobs.queue, jobs.dedupe_key)
        .where(jobs.dedupe_key.is_not_null() & jobs.state.in_(DEDUPE_STATES))
        .do_update(updated_at=jobs.updated_at)
        .returning(jobs.id)
    )


def _acquire_slot(worker: str, token: str, job_id: int, leased_until: datetime.datetime):
    """rqueue's slot acquire: a compare-and-swap over a live lease."""
    return (
        insert_into(slots)
        .values(
            key="concurrency:emails",
            job_id=job_id,
            lease_token=token,
            worker_id=worker,
            acquired_at=transaction_timestamp(),
            leased_until=leased_until,
        )
        .on_conflict(slots.key)
        .do_update(
            job_id=excluded(slots.job_id),
            lease_token=excluded(slots.lease_token),
            worker_id=excluded(slots.worker_id),
            acquired_at=transaction_timestamp(),
            leased_until=excluded(slots.leased_until),
        )
        .where(slots.leased_until.lte(transaction_timestamp()))
        .returning(slots.worker_id)
    )


async def test_arbiter_predicate_selects_the_partial_unique_index(
    conflict_database: PostgresDatabase,
) -> None:
    database = conflict_database
    assert await database.fetch_all(_dedupe_insert("pending")) == [(1,)]

    # The stored row is still inside the partial index, so the insert conflicts
    # and updates rather than adding a duplicate.
    assert await database.fetch_all(_dedupe_insert("pending")) == [(1,)]
    assert await database.fetch_all(select(jobs.id).from_(jobs)) == [(1,)]

    # Moving the row out of the index's predicate makes it invisible to the
    # arbiter, so the very same statement inserts a second row.  Its id is not
    # 2: the conflicting attempt above already consumed an identity value.
    await database.execute(update(jobs).values(state="done").where(jobs.id.eq(1)))
    inserted = await database.fetch_all(_dedupe_insert("pending"))
    assert inserted != [(1,)]
    assert await database.fetch_all(
        select(jobs.id, jobs.state).from_(jobs).order_by(jobs.id.asc())
    ) == [(1, "done"), (inserted[0][0], "pending")]


async def test_arbiter_predicate_survives_a_cached_generic_plan(
    conflict_database: PostgresDatabase,
) -> None:
    """A parameterized arbiter predicate stops matching once the plan goes generic.

    PostgreSQL switches a prepared statement to a generic plan after five
    executions, and asyncpg prepares every statement relq sends.  The arbiter
    predicate therefore has to render constants; running the same statement
    well past the switch is what proves it does.
    """
    database = conflict_database
    identifiers = [await database.fetch_all(_dedupe_insert("pending")) for _ in range(12)]
    assert identifiers == [[(1,)]] * 12
    assert await database.fetch_all(select(jobs.id).from_(jobs)) == [(1,)]

    compiled = compile_postgres(_dedupe_insert("pending"))
    assert (
        'on conflict ("queue", "dedupe_key") where '
        '(("relq_integration_jobs"."dedupe_key" is not null) and '
        "(\"relq_integration_jobs\".\"state\" in (E'pending', E'leased')))" in compiled.sql
    )
    assert compiled.parameters == ("emails", "welcome:7", "pending")


async def test_arbiter_predicate_is_required_by_a_partial_index(
    conflict_database: PostgresDatabase,
) -> None:
    database = conflict_database
    await database.fetch_all(_dedupe_insert("pending"))
    unqualified = (
        insert_into(jobs)
        .values(queue="emails", dedupe_key="welcome:7", state="pending")
        .on_conflict(jobs.queue, jobs.dedupe_key)
        .do_update(updated_at=jobs.updated_at)
        .returning(jobs.id)
    )
    with pytest.raises(asyncpg.InvalidColumnReferenceError, match="no unique or exclusion"):
        await database.fetch_all(unqualified)


async def test_do_update_predicate_keeps_the_slot_acquire_a_compare_and_swap(
    conflict_database: PostgresDatabase,
) -> None:
    database = conflict_database
    live = _now() + datetime.timedelta(hours=1)
    assert await database.fetch_all(_acquire_slot("worker-1", "token-1", 1, live)) == [
        ("worker-1",)
    ]

    # The lease is live, so the predicate is false: no row is updated, nothing
    # is returned, and the original holder keeps the slot.
    stolen = _acquire_slot("worker-2", "token-2", 2, _now() + datetime.timedelta(hours=1))
    assert await database.fetch_all(stolen) == []
    assert await database.fetch_all(
        select(slots.worker_id, slots.lease_token, slots.job_id).from_(slots)
    ) == [("worker-1", "token-1", 1)]

    # Expire the lease and the same statement now takes the slot over.
    await database.execute(
        update(slots)
        .values(leased_until=_now() - datetime.timedelta(minutes=1))
        .where(slots.key.eq("concurrency:emails"))
    )
    assert await database.fetch_all(stolen) == [("worker-2",)]
    assert await database.fetch_all(
        select(slots.worker_id, slots.lease_token, slots.job_id).from_(slots)
    ) == [("worker-2", "token-2", 2)]


async def test_an_unconditional_do_update_would_overwrite_a_live_lease(
    conflict_database: PostgresDatabase,
) -> None:
    """The predicate, not the conflict target, is what protects exclusivity."""
    database = conflict_database
    live = _now() + datetime.timedelta(hours=1)
    await database.fetch_all(_acquire_slot("worker-1", "token-1", 1, live))
    unconditional = (
        insert_into(slots)
        .values(
            key="concurrency:emails",
            job_id=2,
            lease_token="token-2",
            worker_id="worker-2",
            acquired_at=transaction_timestamp(),
            leased_until=live,
        )
        .on_conflict(slots.key)
        .do_update(worker_id=excluded(slots.worker_id))
        .returning(slots.worker_id)
    )
    assert await database.fetch_all(unconditional) == [("worker-2",)]


def test_conflict_predicates_render_in_clause_order_and_reject_sqlite() -> None:
    acquire = _acquire_slot("worker-1", "token-1", 1, _now())
    compiled = compile_postgres(acquire)
    assert (
        'on conflict ("key") do update set "job_id" = excluded."job_id", '
        '"lease_token" = excluded."lease_token", "worker_id" = excluded."worker_id", '
        '"acquired_at" = transaction_timestamp(), "leased_until" = excluded."leased_until" '
        'where ("relq_integration_slots"."leased_until" <= transaction_timestamp()) '
        'returning "relq_integration_slots"."worker_id"' in compiled.sql
    )
    with pytest.raises(ValueError, match="sqlite does not support ON CONFLICT predicates"):
        compile_sqlite(acquire)
    with pytest.raises(ValueError, match="sqlite does not support ON CONFLICT predicates"):
        compile_sqlite(_dedupe_insert("pending"))
