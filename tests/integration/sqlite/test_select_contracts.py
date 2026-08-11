import datetime
import sqlite3
from dataclasses import dataclass

import pytest
from relq import (
    Column,
    SelectQuery,
    Table,
    add,
    case_when,
    coalesce,
    column,
    count,
    now,
    nullif,
    row_adapter,
    select,
    subtract_interval,
    sum,
)
from relq._compiler import compile_postgres, compile_sqlite
from relq_sqlite import SQLiteDatabase


class Users(Table):
    id: Column[int] = column(int)
    email: Column[str] = column(str)
    active: Column[bool] = column(bool)


users = Users("users")


def test_query_values_hide_compiler_state_and_reject_direct_construction() -> None:
    query = select(users.id)
    assert not hasattr(query, "state")
    with pytest.raises(TypeError, match="created by relq builders"):
        SelectQuery()


def test_sqlite_compilation_and_execution() -> None:
    query = (
        select(users.id, users.email)
        .from_(users)
        .where(users.active.is_true())
        .where(users.email.like("%@example.com"))
        .order_by(users.id.desc())
        .limit(2)
    )
    compiled = compile_sqlite(query)
    assert compiled.sql == (
        'select "users"."id", "users"."email" from "users" '
        'where (("users"."active" is true) and ("users"."email" like ?)) '
        'order by "users"."id" desc limit 2'
    )
    assert compiled.parameters == ("%@example.com",)

    connection = sqlite3.connect(":memory:")
    connection.execute("create table users (id integer, email text, active boolean)")
    connection.executemany(
        "insert into users values (?, ?, ?)",
        [(1, "a@example.com", True), (2, "other@invalid", True), (3, "b@example.com", False)],
    )
    assert SQLiteDatabase(connection).fetch_all(query) == [(1, "a@example.com")]


def test_postgres_placeholders_are_positional() -> None:
    query = select(users.id).from_(users).where(users.id.eq(7)).where(users.active.is_true())
    compiled = compile_postgres(query)
    assert "$1" in compiled.sql
    assert "$2" not in compiled.sql
    assert compiled.parameters == (7,)


def test_postgres_row_locking_and_timestamp_arithmetic_are_closed_features() -> None:
    manager = users.as_("manager")
    locked = (
        select(users.id)
        .from_(users)
        .inner_join(manager, on=users.id.eq(manager.id))
        .where(users.active.is_true())
        .order_by(users.id.asc())
        .limit(1)
        .for_update(of=users, skip_locked=True)
    )
    assert compile_postgres(locked).sql == (
        'select "users"."id" from "users" inner join "users" as "manager" '
        'on ("users"."id" = "manager"."id") where ("users"."active" is true) '
        'order by "users"."id" asc limit 1 for update of "users" skip locked'
    )
    with pytest.raises(ValueError, match="sqlite does not support FOR UPDATE"):
        compile_sqlite(locked)  # pyright: ignore[reportArgumentType]

    recent = (
        select(users.id)
        .from_(users)
        .where(users.id.gt(0) & subtract_interval(now(), datetime.timedelta(days=7)).lt(now()))
    )
    assert compile_postgres(recent).sql == (
        'select "users"."id" from "users" where (("users"."id" > $1) and ((now() - $2::interval) < now()))'
    )
    assert compile_postgres(recent).parameters == (0, datetime.timedelta(days=7))
    with pytest.raises(ValueError, match="sqlite does not support timestamp/duration arithmetic"):
        compile_sqlite(recent)


def test_for_update_rejects_non_lockable_query_shapes_and_unknown_tables() -> None:
    class Accounts(Table):
        id: Column[int] = column(int)

    accounts = Accounts("accounts")
    with pytest.raises(ValueError, match="DISTINCT"):
        compile_postgres(select(users.id).from_(users).distinct().for_update())
    with pytest.raises(ValueError, match="aggregate"):
        compile_postgres(select(count()).from_(users).for_update())
    with pytest.raises(ValueError, match="direct table"):
        compile_postgres(select(users.id).from_(users).for_update(of=accounts))
    with pytest.raises(ValueError, match=r"for_update\(\) can only"):
        select(users.id).from_(users).for_update().for_update()


def test_grouped_aggregate_query_compiles_and_executes() -> None:
    total = count().as_("total")
    query = (
        select(users.active, total)
        .from_(users)
        .group_by(users.active)
        .having(count().eq(1))
        .order_by(users.active.asc())
    )
    compiled = compile_sqlite(query)
    assert compiled.sql == (
        'select "users"."active", count(*) as "total" from "users" '
        'group by "users"."active" having (count(*) = ?) order by "users"."active" asc'
    )
    assert compiled.parameters == (1,)

    connection = sqlite3.connect(":memory:")
    connection.execute("create table users (id integer, email text, active boolean)")
    connection.executemany(
        "insert into users values (?, ?, ?)",
        [(1, "a@example.com", True), (2, "b@example.com", False), (3, "c@example.com", False)],
    )
    assert SQLiteDatabase(connection).fetch_all(query) == [(1, 1)]


def test_distinct_and_nullable_aggregate_result() -> None:
    query = select(sum(users.id)).from_(users).distinct()
    compiled = compile_postgres(query)
    assert compiled.sql == 'select distinct sum("users"."id") from "users"'
    assert compiled.parameters == ()


def test_rejects_columns_from_tables_outside_the_source_scope() -> None:
    class Accounts(Table):
        id: Column[int] = column(int)

    accounts = Accounts("accounts")
    query = select(users.id, accounts.id).from_(users)
    with pytest.raises(ValueError, match="accounts"):
        compile_sqlite(query)


def test_outer_join_requires_an_explicit_nullable_result_marker() -> None:
    class Accounts(Table):
        id: Column[int] = column(int)

    accounts = Accounts("accounts")
    unmarked = (
        select(users.id, accounts.id).from_(users).left_join(accounts, on=users.id.eq(accounts.id))
    )
    with pytest.raises(ValueError, match=r"accounts.*\.nullable\(\)"):
        compile_sqlite(unmarked)

    marked = (
        select(users.id, accounts.id.nullable())
        .from_(users)
        .left_join(accounts, on=users.id.eq(accounts.id))
    )
    assert compile_sqlite(marked).sql == (
        'select "users"."id", "accounts"."id" from "users" '
        'left join "accounts" on ("users"."id" = "accounts"."id")'
    )


def test_outer_join_requires_marker_for_arithmetic_results() -> None:
    class Accounts(Table):
        id: Column[int] = column(int)

    accounts = Accounts("accounts")
    query = (
        select(add(accounts.id, 1).nullable())
        .from_(users)
        .left_join(accounts, on=users.id.eq(accounts.id))
    )
    assert compile_sqlite(query).sql.startswith('select ("accounts"."id" + ?)')


def test_conditional_expressions_are_closed_and_parameterized() -> None:
    query = (
        select(
            case_when(users.active.is_true(), "active").else_("inactive").as_("state"),
            coalesce(users.email, users.email).as_("fallback"),
            nullif(users.id, 0).as_("non_zero_id"),
        )
        .from_(users)
        .where(users.id.gt(0))
    )
    compiled = compile_sqlite(query)
    assert compiled.sql == (
        'select case when ("users"."active" is true) then ? else ? end as "state", '
        'coalesce("users"."email", "users"."email") as "fallback", '
        'nullif("users"."id", ?) as "non_zero_id" from "users" where ("users"."id" > ?)'
    )
    assert compiled.parameters == ("active", "inactive", 0, 0)


def test_raw_sqlite_rows_are_not_silently_typed_as_python_values() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table users (id integer, email text, active boolean)")
    connection.execute("insert into users values (1, 'a@example.com', 1)")
    database = SQLiteDatabase(connection)
    raw = database.fetch_one(select(users.active).from_(users))
    assert raw == (1,)

    @dataclass(frozen=True)
    class BooleanRow:
        active: bool

    assert database.fetch_one_as(
        select(users.active).from_(users), row_adapter(BooleanRow)
    ) == BooleanRow(True)
    assert not hasattr(select(users.id), "node")
    assert not hasattr(select(users.id), "adapter")


def test_single_assignment_builder_clauses_reject_accidental_replacement() -> None:
    query = select(users.id).from_(users)
    with pytest.raises(ValueError, match=r"from_\(\) can only"):
        query.from_(users)
    with pytest.raises(ValueError, match=r"limit\(\) can only"):
        query.limit(1).limit(2)
    with pytest.raises(ValueError, match=r"offset\(\) can only"):
        query.offset(1).offset(2)
