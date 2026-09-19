"""SQLite execution contracts for mutation queries and their safety states."""

import sqlite3

import pytest
from relq import delete_from, excluded, insert_into, select, update
from relq._compiler import compile_postgres, compile_sqlite
from relq_sqlite import SQLiteDatabase

from tests.fixtures import EmployeeRow, employee_archive, employees


def test_dml_returning_and_execution() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "create table employees (id integer primary key, manager_id integer, name text, salary integer)"
    )
    database = SQLiteDatabase(connection)
    assert database.fetch_all(
        insert_into(employees)
        .values(id=1, manager_id=None, name="Ada", salary=10)
        .returning(employees.id)
    ) == [(1,)]
    assert database.fetch_all(
        update(employees).values(salary=20).where(employees.id.eq(1)).returning(employees.salary)
    ) == [(20,)]
    assert database.fetch_all(
        delete_from(employees).where(employees.id.eq(1)).returning(employees.name)
    ) == [("Ada",)]


def test_dml_requires_explicit_execution_intent() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table employees (id integer primary key, name text, salary integer)")
    database = SQLiteDatabase(connection)
    database.execute(insert_into(employees).values(id=1, name="Ada"))
    with pytest.raises(ValueError, match=r"where\(\) or explicit all_rows"):
        compile_sqlite(update(employees).values(name="Grace"))
    with pytest.raises(ValueError, match=r"where\(\) or explicit all_rows"):
        compile_sqlite(delete_from(employees))
    assert database.execute(update(employees).values(name="Grace").all_rows()) == 1

    returning = update(employees).values(name="Ada").all_rows().returning(employees.id)
    with pytest.raises(TypeError, match="cannot consume RETURNING"):
        database.execute(returning)  # pyright: ignore[reportArgumentType]
    with pytest.raises(TypeError, match="cannot consume RETURNING"):
        database.execute(select(employees.id).from_(employees))  # pyright: ignore[reportArgumentType]


def test_insert_from_select_and_conflict_updates_compile_and_execute() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table employees (id integer primary key, name text not null unique)")
    connection.execute("create table employee_archive (id integer primary key, name text not null)")
    database = SQLiteDatabase(connection)
    database.execute(insert_into(employees).values(id=1, name="Ada"))
    copied = insert_into(employee_archive).from_select(
        select(employees.id, employees.name).from_(employees),
        employee_archive.id,
        employee_archive.name,
    )
    assert database.execute(copied) == 1
    upsert = (
        insert_into(employees)
        .values(id=2, name="Ada")
        .on_conflict(employees.name)
        .do_update(id=excluded(employees.id))
        .returning(employees.id, employees.name)
    )
    assert database.fetch_all(upsert) == [(2, "Ada")]
    assert 'on conflict ("name") do update set "id" = excluded."id"' in compile_sqlite(upsert).sql
    assert (
        database.execute(insert_into(employees).values(id=3, name="Ada").on_conflict().do_nothing())
        == 0
    )


def test_native_multi_row_insert_compiles_once_executes_once_and_has_parameter_guard() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table employees (id integer primary key, name text not null)")
    database = SQLiteDatabase(connection)
    batch = insert_into(employees).values_many(
        ({"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"})
    )
    compiled = compile_sqlite(batch)
    assert compiled.sql == 'insert into "employees" ("id", "name") values (?, ?), (?, ?)'
    assert compiled.parameters == (1, "Ada", 2, "Grace")
    assert database.execute(batch) == 2
    with pytest.raises(ValueError, match="same columns"):
        insert_into(employees).values_many(({"id": 3}, {"id": 4, "name": "Lin"}))
    with pytest.raises(ValueError, match="at most 999 parameters"):
        compile_sqlite(insert_into(employees).values_many({"id": index} for index in range(1_000)))


def test_default_values_is_a_distinct_insert_source() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "create table employees (id integer not null default 7, name text not null default 'Ada')"
    )
    database = SQLiteDatabase(connection)
    command = insert_into(employees).default_values().returning(employees.id, employees.name)
    assert (
        compile_sqlite(command).sql
        == 'insert into "employees" default values returning "employees"."id", "employees"."name"'
    )
    assert compile_postgres(command).sql == (
        'insert into "employees" default values returning "employees"."id", "employees"."name"'
    )
    assert database.fetch_all(command) == [(7, "Ada")]
    with pytest.raises(ValueError, match="source can only be specified once"):
        insert_into(employees).default_values().values(name="Grace")
    with pytest.raises(ValueError, match="mutually exclusive"):
        insert_into(employees).default_values().on_conflict()


def test_declared_result_models_support_wide_projection_and_returning() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "create table employees (id integer primary key, manager_id integer, name text, salary integer)"
    )
    database = SQLiteDatabase(connection)
    database.execute(insert_into(employees).values(id=1, manager_id=None, name="Ada", salary=10))
    assert database.fetch_one(
        update(employees)
        .values(salary=11)
        .returning(employees.id, employees.name)
        .decode(EmployeeRow)
        .where(employees.id.eq(1))
    ) == EmployeeRow(1, "Ada")
