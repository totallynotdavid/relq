"""PostgreSQL-only schemas and environment gate for integration contracts."""

import enum
from dataclasses import dataclass

from relq import AwareDateTime, Column, CteTable, Table, column, output_column

from tests.postgres_harness import PostgresHarness, postgres_harness_from_environment


def configured_harness() -> PostgresHarness:
    harness = postgres_harness_from_environment()
    if harness is None:
        raise RuntimeError("PostgreSQL integration requires RELQ_TEST_POSTGRES_DSN")
    return harness


class IntegrationUsers(Table):
    id: Column[int] = column(int)
    manager_id: Column[int | None] = column(int)
    name: Column[str] = column(str)
    active: Column[bool] = column(bool)
    state: Column[str] = column(str)
    state_history: Column[list[str]] = column(list)


users = IntegrationUsers("relq_integration_users")


class IntegrationArchive(Table):
    id: Column[int] = column(int)
    name: Column[str] = column(str)


archive = IntegrationArchive("relq_integration_archive")


class IntegrationJobs(Table):
    """A partial-unique-index dedupe table, mirroring rqueue's ``jobs``."""

    id: Column[int] = column(int)
    queue: Column[str] = column(str)
    dedupe_key: Column[str | None] = column(str)
    state: Column[str] = column(str)
    updated_at: Column[int] = column(int)


jobs = IntegrationJobs("relq_integration_jobs")


class IntegrationSlots(Table):
    """A lease table whose upsert must stay a compare-and-swap."""

    key: Column[str] = column(str)
    job_id: Column[int] = column(int)
    lease_token: Column[str] = column(str)
    worker_id: Column[str] = column(str)
    acquired_at: Column[AwareDateTime] = column(AwareDateTime)
    leased_until: Column[AwareDateTime] = column(AwareDateTime)


slots = IntegrationSlots("relq_integration_slots")


class Active(CteTable):
    id: Column[int] = output_column(int)


class UnexpectedState(enum.StrEnum):
    OTHER = "other"


@dataclass(frozen=True)
class DecodedState:
    state: UnexpectedState
