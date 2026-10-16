"""Closed PostgreSQL temporal expression and dialect-boundary contracts."""

import datetime
from collections.abc import Callable

import pytest
from relq import (
    AwareDateTime,
    AwareTime,
    Column,
    CteTable,
    DerivedTable,
    ExtractField,
    Interval,
    NaiveDateTime,
    NaiveTime,
    Table,
    TruncUnit,
    add_interval,
    age,
    at_time_zone,
    clock_timestamp,
    column,
    cte,
    current_date,
    current_time,
    date_bin,
    date_difference,
    date_trunc,
    delete_from,
    divide_interval,
    exists,
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
    scalar,
    select,
    statement_timestamp,
    subtract_interval,
    time_difference,
    timestamp_difference,
    to_timestamp,
    transaction_timestamp,
    update,
)
from relq._compiler import compile_postgres, compile_sqlite


class TemporalRows(Table):
    id: Column[int] = column()
    date_value: Column[datetime.date] = column()
    naive_value: Column[NaiveDateTime] = column()
    aware_value: Column[AwareDateTime] = column()
    naive_time: Column[NaiveTime] = column()
    aware_time: Column[AwareTime] = column()
    interval_value: Column[Interval] = column()


class TemporalCte(CteTable):
    value: Column[NaiveDateTime] = output_column()


class TemporalDerived(DerivedTable):
    value: Column[NaiveDateTime] = output_column()


temporal_rows = TemporalRows("temporal_rows")


def test_temporal_nodes_cover_the_complete_closed_builder_surface() -> None:
    clocks = select(
        transaction_timestamp(),
        statement_timestamp(),
        clock_timestamp(),
        current_date(),
        current_time(),
        local_time(),
        local_timestamp(),
    ).from_(temporal_rows)
    clock_sql = compile_postgres(clocks).sql
    assert "transaction_timestamp()" in clock_sql
    assert "statement_timestamp()" in clock_sql
    assert "clock_timestamp()" in clock_sql
    assert "current_date, current_time, localtime, localtimestamp" in clock_sql

    constructors = select(
        make_date(2026, 8, 19),
        make_time(12, 30, 0.5),
        make_timestamp(2026, 8, 19, 12, 30, 0.5),
        make_timestamptz(2026, 8, 19, 12, 30, 0.5),
        to_timestamp(1.5),
        make_interval(),
        make_interval(years=1, weeks=2, hours=3, minutes=4, seconds=5.5),
    ).from_(temporal_rows)
    constructor_sql = compile_postgres(constructors)
    assert "make_date($1, $2, $3)" in constructor_sql.sql
    assert "make_time($4, $5, $6)" in constructor_sql.sql
    assert "make_timestamp($7, $8, $9, $10, $11, $12)" in constructor_sql.sql
    assert "make_timestamptz($13, $14, $15, $16, $17, $18)" in constructor_sql.sql
    assert "to_timestamp($19)" in constructor_sql.sql
    assert "make_interval()" in constructor_sql.sql
    assert (
        "make_interval(years => $20, weeks => $21, hours => $22, mins => $23, secs => $24)"
        in constructor_sql.sql
    )
    zoned_constructor = compile_postgres(
        select(make_timestamptz(2026, 8, 19, 12, 30, 0.5, "America/Lima")).from_(temporal_rows)
    )
    assert (
        zoned_constructor.sql
        == 'select make_timestamptz($1, $2, $3, $4, $5, $6, $7) from "temporal_rows"'
    )
    assert zoned_constructor.parameters == (2026, 8, 19, 12, 30, 0.5, "America/Lima")

    intervals = select(
        justify_days(temporal_rows.interval_value),
        justify_hours(temporal_rows.interval_value),
        justify_interval(temporal_rows.interval_value),
        date_trunc(TruncUnit.DAY, temporal_rows.interval_value),
    ).from_(temporal_rows)
    interval_sql = compile_postgres(intervals).sql
    assert 'justify_days("temporal_rows"."interval_value")' in interval_sql
    assert 'justify_hours("temporal_rows"."interval_value")' in interval_sql
    assert 'justify_interval("temporal_rows"."interval_value")' in interval_sql
    assert 'date_trunc(\'day\', "temporal_rows"."interval_value")' in interval_sql


def test_temporal_nodes_render_as_semantic_postgres_operations() -> None:
    arithmetic = select(
        add_interval(temporal_rows.naive_value, Interval(days=1)),
        subtract_interval(temporal_rows.aware_value, Interval(days=1)),
        date_difference(temporal_rows.date_value, temporal_rows.date_value),
        time_difference(temporal_rows.naive_time, temporal_rows.naive_time),
        timestamp_difference(temporal_rows.naive_value, temporal_rows.naive_value),
        negate_interval(temporal_rows.interval_value),
        multiply_interval(temporal_rows.interval_value, 2),
        divide_interval(temporal_rows.interval_value, 2),
    ).from_(temporal_rows)
    assert compile_postgres(arithmetic).parameters == (
        Interval(days=1),
        Interval(days=1),
        2,
        2,
    )

    calendar = select(
        at_time_zone(temporal_rows.naive_value, "America/Lima"),
        at_time_zone(temporal_rows.aware_time, "UTC"),
        extract(ExtractField.YEAR, temporal_rows.naive_value),
        extract(ExtractField.JULIAN, temporal_rows.naive_value),
        date_trunc(TruncUnit.DAY, temporal_rows.aware_value, "America/Lima"),
        date_bin(Interval(days=1), temporal_rows.naive_value, temporal_rows.naive_value),
        age(temporal_rows.naive_value, temporal_rows.naive_value),
        overlaps(
            temporal_rows.naive_value,
            temporal_rows.naive_value,
            temporal_rows.naive_value,
            temporal_rows.naive_value,
        ),
    ).from_(temporal_rows)
    compiled = compile_postgres(calendar)
    assert "at time zone $1" in compiled.sql
    assert compiled.parameters == (
        "America/Lima",
        "UTC",
        "America/Lima",
        Interval(days=1),
    )


@pytest.mark.parametrize(
    "stride",
    (
        Interval(months=1),
        Interval(),
        Interval(days=-1),
        Interval(days=1, microseconds=-86_400_000_000),
    ),
)
def test_date_bin_rejects_known_invalid_literal_strides(stride: Interval) -> None:
    query = select(date_bin(stride, temporal_rows.naive_value, temporal_rows.naive_value)).from_(
        temporal_rows
    )
    with pytest.raises(
        ValueError,
        match="date_bin stride must be positive and cannot contain month-or-larger units",
    ):
        compile_postgres(query)


_PLUS_A_DAY = add_interval(temporal_rows.naive_value, Interval(days=1))
_OTHER = temporal_rows.as_("other")
_NESTED = select(temporal_rows.id).from_(temporal_rows).where(_PLUS_A_DAY.is_not_null())
_DERIVED = (
    select(_PLUS_A_DAY.as_("value")).from_(temporal_rows).as_(TemporalDerived, "temporal_derived")
)
_CTE = cte(TemporalCte, "temporal_cte")

_SQLITE_REJECTED: dict[str, Callable[[], object]] = {
    "join predicate": lambda: (
        select(temporal_rows.id)
        .from_(temporal_rows)
        .inner_join(_OTHER, on=_PLUS_A_DAY.eq(_OTHER.naive_value))
    ),
    "subquery in a join predicate": lambda: (
        select(temporal_rows.id)
        .from_(temporal_rows)
        .inner_join(
            _OTHER,
            on=_OTHER.naive_value.eq(scalar(select(local_timestamp()).from_(temporal_rows))),
        )
    ),
    "update values": lambda: (
        update(temporal_rows).values(naive_value=_PLUS_A_DAY).where(temporal_rows.id.eq(1))
    ),
    "update returning": lambda: (
        update(temporal_rows)
        .values(naive_value=temporal_rows.naive_value)
        .where(temporal_rows.id.eq(1))
        .returning(_PLUS_A_DAY)
    ),
    "in subquery": lambda: (
        select(temporal_rows.id).from_(temporal_rows).where(temporal_rows.id.in_(_NESTED))
    ),
    "exists subquery": lambda: (
        select(temporal_rows.id)
        .from_(temporal_rows)
        .where(
            temporal_rows.id.eq(temporal_rows.id)
            & exists(select(local_timestamp()).from_(temporal_rows).where(temporal_rows.id.eq(1)))
        )
    ),
    "scalar subquery": lambda: (
        select(temporal_rows.id).from_(temporal_rows).where(temporal_rows.id.eq(scalar(_NESTED)))
    ),
    "upsert update": lambda: (
        insert_into(temporal_rows)
        .values(id=1, naive_value=temporal_rows.naive_value)
        .on_conflict(temporal_rows.id)
        .do_update(naive_value=_PLUS_A_DAY)
    ),
    "insert returning": lambda: (
        insert_into(temporal_rows)
        .values(id=1, naive_value=temporal_rows.naive_value)
        .returning(_PLUS_A_DAY)
    ),
    "delete returning": lambda: (
        delete_from(temporal_rows).where(temporal_rows.id.eq(1)).returning(_PLUS_A_DAY)
    ),
    "insert from select": lambda: insert_into(temporal_rows).from_select(
        select(_PLUS_A_DAY).from_(temporal_rows), temporal_rows.naive_value
    ),
    "cte body": lambda: (
        select(_CTE.value)
        .from_(_CTE)
        .with_(_CTE, select(_PLUS_A_DAY.as_("value")).from_(temporal_rows))
    ),
    "derived table": lambda: select(_DERIVED.value).from_(_DERIVED),
    "compound arm": lambda: (
        select(temporal_rows.naive_value)
        .from_(temporal_rows)
        .union_all(select(_PLUS_A_DAY).from_(temporal_rows))
    ),
}


@pytest.mark.parametrize("build", _SQLITE_REJECTED.values(), ids=_SQLITE_REJECTED.keys())
def test_sqlite_rejects_temporal_nodes_wherever_they_appear(build: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(build())  # pyright: ignore[reportArgumentType]


def test_overlaps_needs_no_nullable_marker_after_an_outer_join() -> None:
    # overlaps() is a NullablePredicate already, so an outer join cannot widen it.
    other = temporal_rows.as_("other")
    on = temporal_rows.id.eq(other.id)
    overlapping = overlaps(
        other.naive_value, other.naive_value, other.naive_value, other.naive_value
    )

    compiled = compile_postgres(
        select(overlapping.as_("hit")).from_(temporal_rows).left_join(other, on=on)
    )
    assert "overlaps" in compiled.sql

    with pytest.raises(ValueError, match="NULL-extend"):
        compile_postgres(
            select(age(other.naive_value, other.naive_value))
            .from_(temporal_rows)
            .left_join(other, on=on)
        )
