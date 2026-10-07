"""PostgreSQL-only schemas and environment gate for integration contracts."""

import datetime
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
    id: Column[int] = column()
    manager_id: Column[int | None] = column()
    name: Column[str] = column()
    active: Column[bool] = column()
    state: Column[str] = column()
    state_history: Column[list[str]] = column()


users = IntegrationUsers("relq_integration_users")


class IntegrationArchive(Table):
    id: Column[int] = column()
    name: Column[str] = column()


archive = IntegrationArchive("relq_integration_archive")


class IntegrationJobs(Table):
    """A partial-unique-index dedupe table, mirroring rqueue's ``jobs``."""

    id: Column[int] = column()
    queue: Column[str] = column()
    dedupe_key: Column[str | None] = column()
    state: Column[str] = column()
    updated_at: Column[int] = column()


jobs = IntegrationJobs("relq_integration_jobs")


class IntegrationSlots(Table):
    """A lease table whose upsert must stay a compare-and-swap."""

    key: Column[str] = column()
    job_id: Column[int] = column()
    lease_token: Column[str] = column()
    worker_id: Column[str] = column()
    acquired_at: Column[AwareDateTime] = column()
    leased_until: Column[AwareDateTime] = column()


slots = IntegrationSlots("relq_integration_slots")


class IntegrationLedger(Table):
    """A composite arbiter wider than any per-position overload ladder."""

    tenant: Column[int] = column()
    queue: Column[str] = column()
    dedupe_key: Column[str | None] = column()
    epoch: Column[int] = column()
    day: Column[datetime.date] = column()
    state: Column[str] = column()
    revision: Column[int] = column()


ledger = IntegrationLedger("relq_integration_ledger")


class Active(CteTable):
    id: Column[int] = output_column()


class UnexpectedState(enum.StrEnum):
    OTHER = "other"


@dataclass(frozen=True)
class DecodedState:
    state: UnexpectedState
