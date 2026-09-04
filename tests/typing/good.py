import datetime
import decimal
from dataclasses import dataclass
from typing import Literal, assert_type

from relq import (
    AggregateExpr,
    AwareDateTime,
    AwareTime,
    CaseWhen,
    Column,
    ConflictUpdateQuery,
    CteTable,
    DerivedTable,
    Expr,
    ExtractField,
    InsertQuery,
    Interval,
    NaiveDateTime,
    NaiveTime,
    NullablePredicate,
    Order,
    Predicate,
    SelectQuery,
    Table,
    TruncUnit,
    WindowExclusion,
    WindowSpec,
    add,
    add_interval,
    age,
    at_time_zone,
    case_when,
    clock_timestamp,
    coalesce,
    column,
    count,
    cte,
    cume_dist,
    current_date,
    current_row,
    current_time,
    date_bin,
    date_difference,
    date_trunc,
    divide,
    divide_interval,
    excluded,
    extract,
    insert_into,
    justify_days,
    justify_hours,
    justify_interval,
    local_time,
    local_timestamp,
    make_date,
    make_interval,
    make_time,
    make_timestamp,
    make_timestamptz,
    multiply_interval,
    negate_interval,
    output_column,
    overlaps,
    percent_rank,
    row_number,
    scalar,
    select,
    statement_timestamp,
    subtract_interval,
    sum,
    time_difference,
    timestamp_difference,
    to_timestamp,
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


class Temporal(Table):
    date_value: Column[datetime.date] = column(datetime.date)
    naive_value: Column[NaiveDateTime] = column(NaiveDateTime)
    aware_value: Column[AwareDateTime] = column(AwareDateTime)
    naive_time: Column[NaiveTime] = column(NaiveTime)
    aware_time: Column[AwareTime] = column(AwareTime)
    interval_value: Column[Interval] = column(Interval)


temporal = Temporal("temporal")

assert_type(transaction_timestamp(), Expr[AwareDateTime])
assert_type(statement_timestamp(), Expr[AwareDateTime])
assert_type(clock_timestamp(), Expr[AwareDateTime])
assert_type(current_date(), Expr[datetime.date])
assert_type(current_time(), Expr[AwareTime])
assert_type(local_time(), Expr[NaiveTime])
assert_type(local_timestamp(), Expr[NaiveDateTime])
assert_type(make_date(2026, 8, 19), Expr[datetime.date])
assert_type(make_time(12, 30, 0.0), Expr[NaiveTime])
assert_type(make_timestamp(2026, 8, 19, 12, 30, 0.0), Expr[NaiveDateTime])
assert_type(make_timestamptz(2026, 8, 19, 12, 30, 0.0), Expr[AwareDateTime])
assert_type(make_timestamptz(2026, 8, 19, 12, 30, 0.0, "UTC"), Expr[AwareDateTime])
assert_type(make_interval(months=1, seconds=0.5), Expr[Interval])
assert_type(to_timestamp(0.0), Expr[AwareDateTime])
assert_type(age(temporal.naive_value, temporal.naive_value), Expr[Interval])
assert_type(age(temporal.aware_value, temporal.aware_value), Expr[Interval])
assert_type(justify_days(temporal.interval_value), Expr[Interval])
assert_type(justify_hours(Interval(microseconds=3_600_000_000)), Expr[Interval])
assert_type(justify_interval(temporal.interval_value), Expr[Interval])
assert_type(add_interval(temporal.naive_value, Interval(days=1)), Expr[NaiveDateTime])
assert_type(add_interval(transaction_timestamp(), Interval(days=1)), Expr[AwareDateTime])
assert_type(subtract_interval(transaction_timestamp(), Interval(days=1)), Expr[AwareDateTime])
assert_type(date_difference(temporal.date_value, temporal.date_value), Expr[int])
assert_type(time_difference(temporal.naive_time, temporal.naive_time), Expr[Interval])
assert_type(time_difference(temporal.aware_time, temporal.aware_time), Expr[Interval])
assert_type(timestamp_difference(temporal.naive_value, temporal.naive_value), Expr[Interval])
assert_type(timestamp_difference(temporal.aware_value, temporal.aware_value), Expr[Interval])
assert_type(negate_interval(temporal.interval_value), Expr[Interval])
assert_type(multiply_interval(temporal.interval_value, 2), Expr[Interval])
assert_type(divide_interval(temporal.interval_value, 2), Expr[Interval])
assert_type(at_time_zone(temporal.naive_value, "UTC"), Expr[AwareDateTime])
assert_type(at_time_zone(temporal.aware_value, "UTC"), Expr[NaiveDateTime])
assert_type(at_time_zone(temporal.aware_time, "UTC"), Expr[AwareTime])
assert_type(extract(ExtractField.JULIAN, temporal.date_value), Expr[decimal.Decimal])
assert_type(extract(ExtractField.YEAR, temporal.date_value), Expr[decimal.Decimal])
assert_type(extract(ExtractField.HOUR, temporal.naive_time), Expr[decimal.Decimal])
assert_type(date_trunc(TruncUnit.DAY, temporal.naive_value), Expr[NaiveDateTime])
assert_type(date_trunc(TruncUnit.DAY, temporal.aware_value), Expr[AwareDateTime])
assert_type(date_trunc(TruncUnit.DAY, temporal.aware_value, "UTC"), Expr[AwareDateTime])
assert_type(date_trunc(TruncUnit.DAY, temporal.interval_value), Expr[Interval])
assert_type(
    date_bin(Interval(days=1), temporal.naive_value, temporal.naive_value),
    Expr[NaiveDateTime],
)
assert_type(
    date_bin(Interval(days=1), temporal.aware_value, temporal.aware_value),
    Expr[AwareDateTime],
)
assert_type(
    overlaps(
        temporal.date_value,
        temporal.date_value,
        temporal.date_value,
        temporal.date_value,
    ),
    NullablePredicate,
)
assert_type(
    overlaps(
        temporal.aware_time,
        temporal.aware_time,
        temporal.aware_time,
        temporal.aware_time,
    ),
    NullablePredicate,
)

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
assert_type(
    insert_into(users)
    .values(id=1, email="a@example.com", active=True)
    .returning(users.id, users.id, users.id, users.id, users.id, users.id, users.id, users.id),
    InsertQuery[tuple[int, int, int, int, int, int, int, int], Literal[True]],
)


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
assert_type(upsert, ConflictUpdateQuery[tuple[()], Literal[False]])
guarded = (
    insert_into(users)
    .values(id=1, email="a@example.com", active=True)
    .on_conflict(users.email)
    .where(users.active.is_true())
    .do_update(email=excluded(users.email))
    .where(users.active.is_true())
)
assert_type(guarded, InsertQuery[tuple[()], Literal[False]])
assert_type(guarded.returning(users.id), InsertQuery[tuple[int], Literal[True]])


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
    select(users.id, manager_for_result.id.nullable())
    .decode(OuterJoinResult)
    .from_(users)
    .left_join(manager_for_result, on=users.id.eq(manager_for_result.id))
)
assert_type(outer_join_result, SelectQuery[tuple[int, int | None], OuterJoinResult])


def assert_executor_result_types(database: SQLiteDatabase) -> None:
    assert_type(database.fetch_all(query), list[tuple[int, str]])
    assert_type(database.fetch_all(outer_join_result), list[OuterJoinResult])


model_insert = (
    insert_into(users)
    .values(id=3, email="lin@example.com", active=True)
    .returning(users.id, users.id)
    .decode(OuterJoinResult)
)
assert_type(model_insert, InsertQuery[OuterJoinResult, Literal[True]])
