"""PostgreSQL-only schemas and environment gate for integration contracts."""

import enum
from dataclasses import dataclass

from relq import Column, CteTable, Table, column, output_column

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


class Active(CteTable):
    id: Column[int] = output_column(int)


class UnexpectedState(enum.StrEnum):
    OTHER = "other"


@dataclass(frozen=True)
class DecodedState:
    state: UnexpectedState
