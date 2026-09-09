"""The acceptance check: the real cross-schema retention/purge pair compiles."""

import datetime
import uuid

import pytest
from relq import SelectQuery
from relq._compiler import compile_postgres, compile_sqlite

from tests.retention_shapes import delete_queue_job_ids, eligible_queue_job_ids

CUTOFF = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
JOB_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def _eligible() -> SelectQuery[tuple[uuid.UUID]]:
    return eligible_queue_job_ids(
        queue_schema="rqueue",
        compute_schema="compute",
        queue="api.run_simulation",
        terminal_states=["succeeded", "failed"],
        cutoff=CUTOFF,
        compute_job_id_pattern=PATTERN,
        compute_terminal_statuses=["completed", "failed"],
        limit=100,
    )


def _purge() -> SelectQuery[tuple[int]]:
    return delete_queue_job_ids(
        queue_schema="rqueue",
        job_ids=[JOB_ID],
        queue="api.run_simulation",
        terminal_states=["succeeded", "failed"],
        cutoff=CUTOFF,
    )


def test_the_eligibility_query_compiles_to_the_production_statement() -> None:
    compiled = compile_postgres(_eligible())

    assert compiled.sql == (
        'with "candidates" as materialized ('
        'select "q"."id", cast(("q"."payload" ->> $1) as uuid) as "compute_job_id", '
        '"q"."finished_at" from "rqueue"."jobs" as "q" '
        'where (((("q"."queue" = $2) and ("q"."state" in ($3, $4))) '
        'and ("q"."finished_at" < $5)) and (("q"."payload" ->> $6) ~* $7))'
        ') select "candidates"."id" from "candidates" '
        'inner join "compute"."jobs" as "c" on ("c"."id" = "candidates"."compute_job_id") '
        'where ("c"."status" in ($8, $9)) order by "candidates"."finished_at" asc limit 100'
    )
    assert compiled.parameters == (
        "compute_job_id",
        "api.run_simulation",
        "succeeded",
        "failed",
        CUTOFF,
        "compute_job_id",
        PATTERN,
        "completed",
        "failed",
    )


def test_the_purge_query_deletes_and_counts_in_one_statement() -> None:
    compiled = compile_postgres(_purge())

    assert compiled.sql == (
        'with "removed" as (delete from "rqueue"."jobs" '
        'where (((("jobs"."id" in ($1)) and ("jobs"."queue" = $2)) '
        'and ("jobs"."state" in ($3, $4))) and ("jobs"."finished_at" < $5)) '
        'returning "jobs"."id") select count(*) from "removed"'
    )
    assert compiled.parameters == (JOB_ID, "api.run_simulation", "succeeded", "failed", CUTOFF)


def test_neither_retention_query_can_be_compiled_for_sqlite() -> None:
    with pytest.raises(ValueError, match="PostgreSQL-only cast_uuid"):
        compile_sqlite(_eligible())
    with pytest.raises(ValueError, match="does not support data-modifying"):
        compile_sqlite(_purge())
