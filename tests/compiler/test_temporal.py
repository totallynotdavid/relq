"""Closed PostgreSQL temporal expression and dialect-boundary contracts."""

import datetime

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
from relq._ast import (
    TemporalMakeTimestampNode,
    TemporalMakeTimestamptzNode,
    TemporalTruncNode,
    TemporalTruncTimestamptzNode,
)
from relq._compiler import compile_postgres, compile_sqlite


class TemporalRows(Table):
    id: Column[int] = column(int)
    date_value: Column[datetime.date] = column(datetime.date)
    naive_value: Column[NaiveDateTime] = column(NaiveDateTime)
    aware_value: Column[AwareDateTime] = column(AwareDateTime)
    naive_time: Column[NaiveTime] = column(NaiveTime)
    aware_time: Column[AwareTime] = column(AwareTime)
    interval_value: Column[Interval] = column(Interval)


temporal_rows = TemporalRows("temporal_rows")


def test_temporal_ast_has_only_valid_timestamp_and_truncation_shapes() -> None:
    timestamp = make_timestamp(2026, 8, 19, 12, 30, 0.5)
    timestamptz = make_timestamptz(2026, 8, 19, 12, 30, 0.5, "America/Lima")
    truncation = date_trunc(TruncUnit.DAY, temporal_rows.naive_value)
    zoned_truncation = date_trunc(TruncUnit.DAY, temporal_rows.aware_value, "America/Lima")

    assert isinstance(timestamp.node(), TemporalMakeTimestampNode)
    assert isinstance(timestamptz.node(), TemporalMakeTimestamptzNode)
    assert isinstance(truncation.node(), TemporalTruncNode)
    assert isinstance(zoned_truncation.node(), TemporalTruncTimestamptzNode)


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


def test_sqlite_rejects_temporal_nodes_at_every_query_boundary() -> None:
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(
            insert_into(temporal_rows)
            .values(id=1, naive_value=temporal_rows.naive_value)
            .on_conflict(temporal_rows.id)
            .do_update(naive_value=add_interval(temporal_rows.naive_value, Interval(days=1)))
        )
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(
            insert_into(temporal_rows)
            .values(id=1, naive_value=temporal_rows.naive_value)
            .returning(add_interval(temporal_rows.naive_value, Interval(days=1)))
        )
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(
            delete_from(temporal_rows)
            .where(temporal_rows.id.eq(1))
            .returning(add_interval(temporal_rows.naive_value, Interval(days=1)))
        )
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(
            insert_into(temporal_rows).from_select(
                select(add_interval(temporal_rows.naive_value, Interval(days=1))).from_(
                    temporal_rows
                ),
                temporal_rows.naive_value,
            )
        )

    class TemporalCte(CteTable):
        value: Column[NaiveDateTime] = output_column(NaiveDateTime)

    temporal_cte = cte(TemporalCte, "temporal_cte")
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(
            select(temporal_cte.value)
            .from_(temporal_cte)
            .with_(
                temporal_cte,
                select(
                    add_interval(temporal_rows.naive_value, Interval(days=1)).as_("value")
                ).from_(temporal_rows),
            )
        )

    class TemporalDerived(DerivedTable):
        value: Column[NaiveDateTime] = output_column(NaiveDateTime)

    temporal_derived = (
        select(add_interval(temporal_rows.naive_value, Interval(days=1)).as_("value"))
        .from_(temporal_rows)
        .as_(TemporalDerived, "temporal_derived")
    )
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(select(temporal_derived.value).from_(temporal_derived))

    scalar_query = (
        select(temporal_rows.id)
        .from_(temporal_rows)
        .where(add_interval(temporal_rows.naive_value, Interval(days=1)).is_not_null())
    )
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(
            select(temporal_rows.id)
            .from_(temporal_rows)
            .where(temporal_rows.id.eq(scalar(scalar_query)))
        )
    with pytest.raises(ValueError, match="sqlite does not support PostgreSQL temporal expressions"):
        compile_sqlite(
            select(temporal_rows.naive_value)
            .from_(temporal_rows)
            .union_all(
                select(add_interval(temporal_rows.naive_value, Interval(days=1))).from_(
                    temporal_rows
                )
            )
        )
