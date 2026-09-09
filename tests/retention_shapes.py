"""The cross-schema retention/purge queries used as this feature's target shape.

These reproduce a real production pair of statements: a materialized CTE that
reads a runtime-selected queue schema, extracts a JSON member, guards it with a
case-insensitive regular expression, casts it to ``uuid``, and joins it to a
table in a second schema; then a data-modifying CTE that deletes the selected
rows and counts them in the same statement.

Both schemas are parameters rather than constants because the queue schema is
chosen at deployment time, which is exactly why the SQL spelling of a schema
has to be part of the table declaration instead of the table's name.
"""

import datetime
import uuid
from collections.abc import Sequence

from relq import (
    Column,
    CteTable,
    JsonValue,
    SelectQuery,
    Table,
    column,
    count,
    cte,
    delete_from,
    json_column,
    output_column,
    select,
)
from relq.postgres import cast_uuid, json_text, regex_match

_COMPUTE_JOB_ID = "compute_job_id"


class QueueJobs(Table):
    id: Column[uuid.UUID] = column(uuid.UUID)
    queue: Column[str] = column(str)
    state: Column[str] = column(str)
    payload: Column[JsonValue] = json_column()
    finished_at: Column[datetime.datetime] = column(datetime.datetime)


class ComputeJobs(Table):
    id: Column[uuid.UUID] = column(uuid.UUID)
    status: Column[str] = column(str)


class Candidates(CteTable):
    id: Column[uuid.UUID] = output_column(uuid.UUID)
    compute_job_id: Column[uuid.UUID | None] = output_column(uuid.UUID)
    finished_at: Column[datetime.datetime] = output_column(datetime.datetime)


class Removed(CteTable):
    id: Column[uuid.UUID] = output_column(uuid.UUID)


def eligible_queue_job_ids(
    *,
    queue_schema: str,
    compute_schema: str,
    queue: str,
    terminal_states: Sequence[str],
    cutoff: datetime.datetime,
    compute_job_id_pattern: str,
    compute_terminal_statuses: Sequence[str],
    limit: int,
) -> SelectQuery[tuple[uuid.UUID]]:
    """Find old queue rows whose compute records are already terminal."""
    queue_jobs = QueueJobs("jobs", schema=queue_schema).as_("q")
    compute_jobs = ComputeJobs("jobs", schema=compute_schema).as_("c")
    candidates = cte(Candidates, "candidates")
    compute_job_id = json_text(queue_jobs.payload, _COMPUTE_JOB_ID)
    candidate_rows = (
        select(
            queue_jobs.id,
            cast_uuid(compute_job_id).as_(_COMPUTE_JOB_ID),
            queue_jobs.finished_at,
        )
        .from_(queue_jobs)
        .where(
            queue_jobs.queue.eq(queue)
            & queue_jobs.state.in_(terminal_states)
            & queue_jobs.finished_at.lt(cutoff)
            & regex_match(compute_job_id, compute_job_id_pattern, insensitive=True)
        )
    )
    return (
        select(candidates.id)
        .from_(candidates)
        .inner_join(compute_jobs, on=compute_jobs.id.eq(candidates.compute_job_id))
        .where(compute_jobs.status.in_(compute_terminal_statuses))
        .order_by(candidates.finished_at.asc())
        .limit(limit)
        .with_(candidates, candidate_rows, materialized=True)
    )


def delete_queue_job_ids(
    *,
    queue_schema: str,
    job_ids: Sequence[uuid.UUID],
    queue: str,
    terminal_states: Sequence[str],
    cutoff: datetime.datetime,
) -> SelectQuery[tuple[int]]:
    """Delete the prechecked queue rows and count them in one statement."""
    queue_jobs = QueueJobs("jobs", schema=queue_schema)
    removed = cte(Removed, "removed")
    deleted = (
        delete_from(queue_jobs)
        .where(
            queue_jobs.id.in_(job_ids)
            & queue_jobs.queue.eq(queue)
            & queue_jobs.state.in_(terminal_states)
            & queue_jobs.finished_at.lt(cutoff)
        )
        .returning(queue_jobs.id)
    )
    return select(count()).from_(removed).with_(removed, deleted)
