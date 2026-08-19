import decimal
from dataclasses import dataclass
from typing import Literal, assert_type

from relq import (
    AggregateExpr,
    AwareDateTime,
    CaseWhen,
    Column,
    CteTable,
    DerivedTable,
    Expr,
    InsertQuery,
    Interval,
    ModelInsertQuery,
    ModelSelectQuery,
    NullablePredicate,
    Order,
    Predicate,
    SelectQuery,
    Table,
    WindowExclusion,
    WindowSpec,
    add,
    add_interval,
    case_when,
    coalesce,
    column,
    count,
    cte,
    cume_dist,
    current_row,
    divide,
    excluded,
    insert_into,
    output_column,
    percent_rank,
    row_number,
    scalar,
    select,
    select_model,
    subtract_interval,
    sum,
    transaction_timestamp,
)
from relq._compiler import compile_postgres
from relq_sqlite import SQLiteDatabase


class Users(Table):
    id: Column[int] = column(int)
    email: Column[str] = column(str)
    active: Column[bool] = column(bool)


users = Users("users")
query = select(users.id, users.email).from_(users).where(users.active.is_true())
rows: list[tuple[int, str]]

aggregate_query = select(users.active, count(), sum(users.id)).from_(users).group_by(users.active)
aggregate_rows: list[tuple[bool, int, int | None]]
assert_type(aggregate_query, SelectQuery[tuple[bool, int, int | None]])
assert_type(count().filter(users.active.is_true()), AggregateExpr[int])

manager = users.as_("manager")
assert_type(manager.id, Column[int])
assert_type(add(users.id, 1), Expr[int])
assert_type(add_interval(transaction_timestamp(), Interval(days=1)), Expr[AwareDateTime])
assert_type(subtract_interval(transaction_timestamp(), Interval(days=1)), Expr[AwareDateTime])
assert_type(divide(users.id, 2), Expr[int])
assert_type(users.id.eq(1), NullablePredicate)
assert_type(users.id.is_null(), Predicate)
assert_type(users.active.is_true(), Predicate)
assert_type(add(users.id.nullable(), 1), Expr[int | float | decimal.Decimal | None])
assert_type(users.id.nullable(), Expr[int | None])
conditional = case_when(users.active.is_true(), "active")
assert_type(conditional, CaseWhen[str])
assert_type(conditional.when(users.active.is_false(), "inactive").else_("unknown"), Expr[str])
assert_type(conditional.else_null(), Expr[str | None])
assert_type(coalesce(users.email.nullable(), users.email.nullable()), Expr[str | None])
assert_type(row_number().over(), WindowSpec[int])
assert_type(percent_rank().over(), WindowSpec[float])
assert_type(cume_dist().over(), WindowSpec[float])
assert_type(users.id.asc().nulls_last(), Order)
assert_type(
    sum(users.id)
    .over()
    .order_by(users.id.asc())
    .rows_between(current_row(), current_row())
    .exclude(WindowExclusion.CURRENT_ROW),
    WindowSpec[int | None],
)
insert_query = (
    insert_into(users).values(id=1, email="a@example.com", active=True).returning(users.id)
)
assert_type(insert_query, InsertQuery[tuple[int], Literal[True]])

copied = insert_into(users).from_select(
    select(users.id, users.email).from_(users), users.id, users.email
)
assert_type(copied, InsertQuery[tuple[()], Literal[False]])
assert_type(insert_into(users).default_values(), InsertQuery[tuple[()], Literal[False]])
assert_type(
    select(users.id).from_(users).intersect(select(users.id).from_(users)), SelectQuery[tuple[int]]
)
assert_type(
    select(users.id).from_(users).except_(select(users.id).from_(users)), SelectQuery[tuple[int]]
)
locked = select(users.id).from_(users).for_update(users).skip_locked()
assert_type(locked, SelectQuery[tuple[int]])
compile_postgres(locked)
upsert = (
    insert_into(users)
    .values(id=1, email="a@example.com", active=True)
    .on_conflict(users.email)
    .do_update(email=excluded(users.email))
)
assert_type(upsert, InsertQuery[tuple[()], Literal[False]])


class Totals(DerivedTable):
    user_id: Column[int] = output_column(int)
    total: Column[int] = output_column(int)


totals = select(users.id.as_("user_id"), count().as_("total")).from_(users).as_(Totals, "totals")
assert_type(totals.user_id, Column[int])
assert_type(totals.total, Column[int])


class Active(CteTable):
    id: Column[int] = output_column(int)


active = cte(Active, "active")
assert_type(active.id, Column[int])
assert_type(scalar(select(users.id).from_(users)), Expr[int | None])


@dataclass(frozen=True)
class OuterJoinResult:
    user_id: int
    manager_id: int | None


manager_for_result = users.as_("manager_for_result")
outer_join_result = (
    select_model(OuterJoinResult, users.id, manager_for_result.id.nullable())
    .from_(users)
    .left_join(manager_for_result, on=users.id.eq(manager_for_result.id))
)
assert_type(outer_join_result, ModelSelectQuery[OuterJoinResult])


def assert_executor_result_types(database: SQLiteDatabase) -> None:
    assert_type(database.fetch_all(query), list[tuple[int, str]])
    assert_type(database.fetch_all(outer_join_result), list[OuterJoinResult])


model_insert = (
    insert_into(users)
    .values(id=3, email="lin@example.com", active=True)
    .returning_model(OuterJoinResult, users.id, users.id)
)
assert_type(model_insert, ModelInsertQuery[OuterJoinResult])
