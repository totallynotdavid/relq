"""Closed PostgreSQL temporal expression and dialect-boundary contracts."""

import datetime

import pytest
from relq import (
    AwareDateTime,
    Column,
    ExtractField,
    Interval,
    NaiveDateTime,
    NaiveTime,
    Table,
    TruncUnit,
    add_interval,
    age,
    at_time_zone,
    column,
    current_date,
    date_bin,
    date_difference,
    date_trunc,
    divide_interval,
    exists,
    extract,
    local_timestamp,
    make_interval,
    make_timestamp,
    multiply_interval,
    negate_interval,
    overlaps,
    select,
    subtract_interval,
    time_difference,
    timestamp_difference,
    update,
)
from relq._compiler import compile_postgres, compile_sqlite


class TemporalRows(Table):
    id: Column[int] = column(int)
    date_value: Column[datetime.date] = column(datetime.date)
    naive_value: Column[NaiveDateTime] = column(NaiveDateTime)
    aware_value: Column[AwareDateTime] = column(AwareDateTime)
    naive_time: Column[NaiveTime] = column(NaiveTime)
    interval_value: Column[Interval] = column(Interval)


temporal_rows = TemporalRows("temporal_rows")


def test_temporal_nodes_render_as_semantic_postgres_operations() -> None:
    constructors = select(
        current_date(),
        local_timestamp(),
        make_timestamp(2026, 8, 18, 12, 30, 0),
        make_interval(months=2, days=3, seconds=1.5),
    ).from_(temporal_rows)
    assert compile_postgres(constructors).parameters == (
        2026,
        8,
        18,
        12,
        30,
        0,
        2,
        3,
        1.5,
    )
    assert (
        "make_interval(months => $7, days => $8, secs => $9)" in compile_postgres(constructors).sql
    )
    assert "current_date, localtimestamp" in compile_postgres(constructors).sql

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
        extract(ExtractField.YEAR, temporal_rows.naive_value),
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
        "America/Lima",
        Interval(days=1),
    )


def test_sqlite_rejects_temporal_nodes_in_dml_and_nested_selects() -> None:
    nested = (
        select(temporal_rows.id)
        .from_(temporal_rows)
        .where(add_interval(temporal_rows.naive_value, Interval(days=1)).is_not_null())
    )
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(
            update(temporal_rows)
            .values(naive_value=add_interval(temporal_rows.naive_value, Interval(days=1)))
            .where(temporal_rows.id.eq(1))
        )
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(
            update(temporal_rows)
            .values(naive_value=temporal_rows.naive_value)
            .where(temporal_rows.id.eq(1))
            .returning(add_interval(temporal_rows.naive_value, Interval(days=1)))
        )
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(
            select(temporal_rows.id).from_(temporal_rows).where(temporal_rows.id.in_(nested))
        )
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(
            select(temporal_rows.id)
            .from_(temporal_rows)
            .where(
                temporal_rows.id.eq(temporal_rows.id)
                & exists(
                    select(local_timestamp()).from_(temporal_rows).where(temporal_rows.id.eq(1))
                )
            )
        )
