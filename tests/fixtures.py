"""Schema declarations shared by contract tests, never production code."""

import datetime
import decimal
import enum
import uuid
from dataclasses import dataclass
from typing import NamedTuple

from relq import Column, CteTable, DerivedTable, Table, column, output_column


class Employees(Table):
    id: Column[int] = column(int)
    manager_id: Column[int | None] = column(int)
    name: Column[str] = column(str)
    salary: Column[int] = column(int)


employees = Employees("employees")


class ManagerTotals(DerivedTable):
    manager_id: Column[int | None] = output_column(int)
    reports: Column[int] = output_column(int)


class ActiveEmployees(CteTable):
    id: Column[int] = output_column(int)


class EmployeeArchive(Table):
    id: Column[int] = column(int)
    name: Column[str] = column(str)


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
    id: Column[str] = column(str)
    amount: Column[str] = column(str)
    occurred_at: Column[str] = column(str)
    payload: Column[str | None] = column(str)
    state: Column[str] = column(str)


decoded_values = DecodedValues("decoded_values")


@dataclass(frozen=True)
class DecodedRow:
    id: uuid.UUID
    amount: decimal.Decimal
    occurred_at: datetime.datetime
    payload: object | None
    state: DecodedState
