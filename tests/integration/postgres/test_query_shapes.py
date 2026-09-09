"""End-to-end PostgreSQL contracts for schemas, DML CTEs, and typed PostgreSQL expressions."""

import datetime
import os
import uuid

import asyncpg
import pytest
from relq import Column, Table, column, count, cte, delete_from, insert_into, select
from relq.postgres import cast_uuid, json_text, regex_match
from relq_postgres import PostgresDatabase

from tests.retention_shapes import (
    ComputeJobs,
    QueueJobs,
    Removed,
    delete_queue_job_ids,
    eligible_queue_job_ids,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RELQ_TEST_POSTGRES_DSN") is None,
    reason="set RELQ_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)

QUEUE = "api.run_simulation"
TERMINAL_STATES = ["succeeded", "failed"]
COMPUTE_TERMINAL_STATUSES = ["completed", "failed"]
UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
CUTOFF = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
OLD = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)

ELIGIBLE = uuid.UUID("11111111-1111-1111-1111-111111111111")
STILL_RUNNING = uuid.UUID("22222222-2222-2222-2222-222222222222")
MALFORMED_PAYLOAD = uuid.UUID("33333333-3333-3333-3333-333333333333")
DONE_COMPUTE = uuid.UUID("aaaaaaaa-1111-1111-1111-111111111111")
BUSY_COMPUTE = uuid.UUID("bbbbbbbb-2222-2222-2222-222222222222")


async def _populate(admin: asyncpg.Connection, queue_schema: str) -> None:
    """Create the two-schema fixture the retention queries were written against."""
    await admin.execute(f"""create table {queue_schema}.jobs (
        id uuid primary key, queue text not null, state text not null,
        payload jsonb not null, finished_at timestamptz not null
    )""")
    await admin.execute("create table jobs (id uuid primary key, status text not null)")
    await admin.executemany(
        f"insert into {queue_schema}.jobs values ($1, $2, $3, $4, $5)",
        [
            (ELIGIBLE, QUEUE, "succeeded", f'{{"compute_job_id": "{DONE_COMPUTE}"}}', OLD),
            (STILL_RUNNING, QUEUE, "succeeded", f'{{"compute_job_id": "{BUSY_COMPUTE}"}}', OLD),
            (MALFORMED_PAYLOAD, QUEUE, "failed", '{"compute_job_id": "not-a-uuid"}', OLD),
        ],
    )
    await admin.executemany(
        "insert into jobs values ($1, $2)",
        [(DONE_COMPUTE, "completed"), (BUSY_COMPUTE, "running")],
    )


async def test_the_retention_pair_selects_and_purges_across_two_schemas(
    postgres_admin: asyncpg.Connection, postgres_schema: str, postgres_queue_schema: str
) -> None:
    await _populate(postgres_admin, postgres_queue_schema)
    database = PostgresDatabase(postgres_admin)

    eligible = await database.fetch_all(
        eligible_queue_job_ids(
            queue_schema=postgres_queue_schema,
            compute_schema=postgres_schema,
            queue=QUEUE,
            terminal_states=TERMINAL_STATES,
            cutoff=CUTOFF,
            compute_job_id_pattern=UUID_PATTERN,
            compute_terminal_statuses=COMPUTE_TERMINAL_STATUSES,
            limit=100,
        )
    )
    assert eligible == [(ELIGIBLE,)]

    purged = await database.fetch_one(
        delete_queue_job_ids(
            queue_schema=postgres_queue_schema,
            job_ids=[row[0] for row in eligible],
            queue=QUEUE,
            terminal_states=TERMINAL_STATES,
            cutoff=CUTOFF,
        )
    )
    assert purged == (1,)
    remaining = await postgres_admin.fetch(f"select id from {postgres_queue_schema}.jobs")
    assert {record["id"] for record in remaining} == {STILL_RUNNING, MALFORMED_PAYLOAD}


async def test_a_delete_cte_runs_once_even_when_the_outer_query_reads_nothing(
    postgres_admin: asyncpg.Connection, postgres_schema: str, postgres_queue_schema: str
) -> None:
    await _populate(postgres_admin, postgres_queue_schema)
    database = PostgresDatabase(postgres_admin)

    assert await database.fetch_one(
        delete_queue_job_ids(
            queue_schema=postgres_queue_schema,
            job_ids=[ELIGIBLE, MALFORMED_PAYLOAD],
            queue=QUEUE,
            terminal_states=TERMINAL_STATES,
            cutoff=CUTOFF,
        )
    ) == (2,)
    survivors = await postgres_admin.fetch(f"select id from {postgres_queue_schema}.jobs")
    assert {record["id"] for record in survivors} == {STILL_RUNNING}


async def test_a_later_data_modifying_cte_consumes_an_earlier_one(
    postgres_admin: asyncpg.Connection, postgres_queue_schema: str
) -> None:
    """PostgreSQL's move-rows shape: one DELETE CTE feeding an INSERT CTE, then a count."""

    class Purged(Table):
        id: Column[uuid.UUID] = column(uuid.UUID)

    await _populate(postgres_admin, postgres_queue_schema)
    await postgres_admin.execute("create table purged (id uuid primary key)")
    queue_jobs = QueueJobs("jobs", schema=postgres_queue_schema)
    purged = Purged("purged")
    removed = cte(Removed, "removed")
    copied = cte(Removed, "copied")

    moved = await PostgresDatabase(postgres_admin).fetch_one(
        select(count())
        .from_(copied)
        .with_(
            removed,
            delete_from(queue_jobs).where(queue_jobs.queue.eq(QUEUE)).returning(queue_jobs.id),
        )
        .with_(
            copied,
            insert_into(purged)
            .from_select(select(removed.id).from_(removed), purged.id)
            .returning(purged.id),
        )
    )

    assert moved == (3,)
    survivors = await postgres_admin.fetch(f"select id from {postgres_queue_schema}.jobs")
    assert survivors == []
    archived = await postgres_admin.fetch("select id from purged")
    assert {record["id"] for record in archived} == {ELIGIBLE, STILL_RUNNING, MALFORMED_PAYLOAD}


async def test_typed_postgresql_expressions_evaluate_as_their_operators(
    postgres_admin: asyncpg.Connection, postgres_queue_schema: str
) -> None:
    await _populate(postgres_admin, postgres_queue_schema)
    queue_jobs = QueueJobs("jobs", schema=postgres_queue_schema)
    database = PostgresDatabase(postgres_admin)
    member = json_text(queue_jobs.payload, "compute_job_id")

    extracted = await database.fetch_all(
        select(member.as_("compute_job_id"))
        .from_(queue_jobs)
        .where(queue_jobs.id.eq(MALFORMED_PAYLOAD))
    )
    assert extracted == [("not-a-uuid",)]

    guarded = await database.fetch_all(
        select(cast_uuid(member).as_("compute_job_id"))
        .from_(queue_jobs)
        .where(regex_match(member, UUID_PATTERN, insensitive=True))
        .order_by(queue_jobs.id.asc())
    )
    assert guarded == [(DONE_COMPUTE,), (BUSY_COMPUTE,)]


async def test_a_schema_qualified_table_addresses_the_namespace_it_declares(
    postgres_admin: asyncpg.Connection, postgres_schema: str, postgres_queue_schema: str
) -> None:
    """Both relations are named "jobs"; only the declared schema tells them apart."""
    await _populate(postgres_admin, postgres_queue_schema)
    database = PostgresDatabase(postgres_admin)
    queue_jobs = QueueJobs("jobs", schema=postgres_queue_schema)
    compute_jobs = ComputeJobs("jobs", schema=postgres_schema)

    assert await database.fetch_all(
        select(queue_jobs.id).from_(queue_jobs).order_by(queue_jobs.id.asc())
    ) == [(ELIGIBLE,), (STILL_RUNNING,), (MALFORMED_PAYLOAD,)]
    assert await database.fetch_all(
        select(compute_jobs.id).from_(compute_jobs).order_by(compute_jobs.id.asc())
    ) == [(DONE_COMPUTE,), (BUSY_COMPUTE,)]


async def test_a_generated_style_schema_bound_table_reaches_a_non_default_namespace(
    postgres_admin: asyncpg.Connection, postgres_queue_schema: str
) -> None:
    """codegen now emits exactly this declaration for a non-default schema."""

    class Jobs(Table):
        id: Column[uuid.UUID] = column(uuid.UUID)
        state: Column[str] = column(str)

    await _populate(postgres_admin, postgres_queue_schema)
    jobs = Jobs("jobs", schema=postgres_queue_schema)

    assert await PostgresDatabase(postgres_admin).fetch_one(
        select(jobs.state).from_(jobs).where(jobs.id.eq(ELIGIBLE))
    ) == ("succeeded",)
