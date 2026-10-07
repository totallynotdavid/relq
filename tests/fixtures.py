"""Schema declarations shared by contract tests, never production code."""

import datetime
import decimal
import enum
import uuid
from dataclasses import dataclass
from typing import NamedTuple

from relq import Column, CteTable, DerivedTable, Table, column, output_column


class Employees(Table):
    id: Column[int] = column()
    manager_id: Column[int | None] = column()
    name: Column[str] = column()
    salary: Column[int] = column()


employees = Employees("employees")


class ManagerTotals(DerivedTable):
    manager_id: Column[int | None] = output_column()
    reports: Column[int] = output_column()


class ActiveEmployees(CteTable):
    id: Column[int] = output_column()


class CompositeKeys(Table):
    """A conflict target wider than any per-position overload ladder."""

    tenant: Column[int] = column()
    queue: Column[str] = column()
    dedupe_key: Column[str | None] = column()
    active: Column[bool] = column()
    epoch: Column[int] = column()
    day: Column[str] = column()
    payload: Column[str] = column()


composite_keys = CompositeKeys("composite_keys")


class EmployeeArchive(Table):
    id: Column[int] = column()
    name: Column[str] = column()


employee_archive = EmployeeArchive("employee_archive")


@dataclass(frozen=True)
class EmployeeRow:
    id: int
    name: str


class EmployeeName(NamedTuple):
    name: str


class DecodedState(enum.StrEnum):
    ACTIVE = "active"


class DecodedValues(Table):
    id: Column[str] = column()
    amount: Column[str] = column()
    occurred_at: Column[str] = column()
    payload: Column[str | None] = column()
    state: Column[str] = column()


decoded_values = DecodedValues("decoded_values")


@dataclass(frozen=True)
class DecodedRow:
    id: uuid.UUID
    amount: decimal.Decimal
    occurred_at: datetime.datetime
    payload: object | None
    state: DecodedState
