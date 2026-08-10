"""Explicit result-model and decoder trust-boundary contracts."""

import datetime
import decimal
import ipaddress
import sqlite3
import uuid

import pytest
from relq import (
    RowDecodingError,
    datetime_decoder,
    decimal_decoder,
    enum_decoder,
    inet_decoder,
    insert_into,
    json_decoder,
    nullable,
    row_adapter,
    select,
    select_model,
    str_decoder,
    uuid_decoder,
)
from relq_sqlite import SQLiteDatabase

from tests.fixtures import (
    DecodedRow,
    DecodedState,
    EmployeeName,
    EmployeeRow,
    decoded_values,
    employees,
)


def test_result_mapping_and_transactions() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table employees (id integer primary key, name text not null)")
    database = SQLiteDatabase(connection)
    with database.transaction():
        database.execute(
            insert_into(employees).values_many(
                ({"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"})
            )
        )
    query = select(employees.id, employees.name).from_(employees).order_by(employees.id.asc())
    adapter = row_adapter(EmployeeRow)
    assert database.fetch_all_as(query, adapter) == [EmployeeRow(1, "Ada"), EmployeeRow(2, "Grace")]
    assert database.fetch_one_as(query.where(employees.id.eq(2)), adapter) == EmployeeRow(
        2, "Grace"
    )
    with pytest.raises(ValueError, match="requires 2 result columns"):
        database.fetch_all_as(select(employees.id).from_(employees), adapter)
    assert database.fetch_all_as(
        select(employees.name).from_(employees), row_adapter(EmployeeName)
    ) == [
        EmployeeName("Ada"),
        EmployeeName("Grace"),
    ]


def test_explicit_row_decoders_are_opt_in_and_diagnose_precisely() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "create table decoded_values (id text, amount text, occurred_at text, payload text, state text)"
    )
    identifier = uuid.uuid4()
    connection.execute(
        "insert into decoded_values values (?, ?, ?, ?, ?)",
        (str(identifier), "12.50", "2026-08-09T12:30:00+00:00", '{"ok": true}', "active"),
    )
    database = SQLiteDatabase(connection)
    adapter = row_adapter(
        DecodedRow,
        decoders=(
            uuid_decoder(),
            decimal_decoder(),
            datetime_decoder(),
            nullable(json_decoder()),
            enum_decoder(DecodedState),
        ),
    )
    assert database.fetch_all(
        select(decoded_values.id, decoded_values.amount).from_(decoded_values)
    ) == [(str(identifier), "12.50")]
    assert database.fetch_one(
        select_model(
            adapter,
            decoded_values.id,
            decoded_values.amount,
            decoded_values.occurred_at,
            decoded_values.payload,
            decoded_values.state,
        ).from_(decoded_values)
    ) == DecodedRow(
        identifier,
        decimal.Decimal("12.50"),
        datetime.datetime(2026, 8, 9, 12, 30, tzinfo=datetime.UTC),
        {"ok": True},
        DecodedState.ACTIVE,
    )
    bad = row_adapter(EmployeeRow, decoders=(uuid_decoder(), str_decoder()))
    with pytest.raises(
        RowDecodingError,
        match=r"EmployeeRow.id at result column 0: expected UUID, received 'not-a-uuid' \(str\)",
    ):
        bad.map(("not-a-uuid", "Ada"))
    invalid_json = row_adapter(EmployeeName, decoders=(json_decoder(),))
    with pytest.raises(
        RowDecodingError,
        match=r"EmployeeName.name at result column 0: expected JSON, received '\{' \(str\)",
    ):
        invalid_json.map(("{",))
    null_uuid = row_adapter(EmployeeName, decoders=(uuid_decoder(),))
    with pytest.raises(
        RowDecodingError,
        match=r"EmployeeName.name at result column 0: expected UUID, received None \(NoneType\)",
    ):
        null_uuid.map((None,))
    assert inet_decoder().decode("203.0.113.7") == ipaddress.IPv4Address("203.0.113.7")
    assert inet_decoder().decode("2001:db8::1/64") == ipaddress.IPv6Interface("2001:db8::1/64")
