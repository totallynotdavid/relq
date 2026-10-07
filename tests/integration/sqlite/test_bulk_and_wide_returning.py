"""Bulk inserts past the SQLite parameter ceiling and RETURNING without an expression limit."""

import sqlite3
from collections.abc import Callable, Iterator

import pytest
from relq import Column, Table, column, delete_from, insert_into, select
from relq._compiler import compile_sqlite
from relq_sqlite import SQLiteDatabase, row_batches

from tests.fixtures import employees


class Wide(Table):
    c1: Column[int] = column()
    c2: Column[int] = column()
    c3: Column[int] = column()
    c4: Column[int] = column()
    c5: Column[int] = column()
    c6: Column[int] = column()
    c7: Column[int] = column()
    c8: Column[int] = column()
    c9: Column[int] = column()
    c10: Column[int] = column()


wide = Wide("wide")


def _call_dynamically(function: Callable[..., object], *arguments: object) -> object:
    """Reach a builder method the way untyped callers do, past its overloads."""
    return function(*arguments)


def _database() -> SQLiteDatabase:
    connection = sqlite3.connect(":memory:", autocommit=True)
    connection.execute("create table employees (id integer primary key, name text not null)")
    connection.execute(
        "create table wide (c1 integer, c2 integer, c3 integer, c4 integer, c5 integer, "
        "c6 integer, c7 integer, c8 integer, c9 integer, c10 integer)"
    )
    return SQLiteDatabase(connection)


def test_one_values_many_statement_stops_at_the_parameter_ceiling() -> None:
    rows = [{"id": index, "name": f"n{index}"} for index in range(600)]

    with pytest.raises(ValueError, match="at most 999 parameters"):
        compile_sqlite(insert_into(employees).values_many(rows))


def test_row_batches_split_by_the_ceiling_and_insert_every_row_once() -> None:
    database = _database()
    rows = [{"id": index, "name": f"n{index}"} for index in range(1_300)]

    batches = list(row_batches(rows))

    assert [len(batch) for batch in batches] == [499, 499, 302]
    for batch in batches:
        assert len(compile_sqlite(insert_into(employees).values_many(batch)).parameters) <= 999
    with database.transaction() as transaction:
        inserted = sum(transaction.execute(insert_into(employees).values_many(b)) for b in batches)
    assert inserted == 1_300
    assert database.fetch_one(select(employees.id).from_(employees).limit(1)) == (0,)


def test_row_batches_of_no_rows_yield_nothing() -> None:
    assert list(row_batches([])) == []


def test_row_batches_read_a_generator_one_batch_at_a_time() -> None:
    consumed = 0

    def stream() -> Iterator[dict[str, object]]:
        nonlocal consumed
        for index in range(2_000):
            consumed += 1
            yield {"id": index, "name": "x"}

    first = next(row_batches(stream()))

    assert len(first) == 499
    assert consumed == 499


def test_row_batches_reject_a_row_wider_than_the_ceiling() -> None:
    row = {f"c{index}": index for index in range(1_000)}

    with pytest.raises(ValueError, match="exceeds the 999-parameter"):
        list(row_batches([row]))


def test_a_row_of_the_wrong_shape_is_still_rejected_inside_a_batch() -> None:
    batch = next(row_batches([{"id": 1, "name": "a"}, {"id": 2}]))

    with pytest.raises(ValueError, match="same columns"):
        insert_into(employees).values_many(batch)


def test_returning_more_than_eight_expressions_names_the_whole_relation_form() -> None:
    # The overloads stop at eight, so a ninth is a type error; this is the runtime
    # guard for callers that reach `returning` dynamically.
    columns = (wide.c1, wide.c2, wide.c3, wide.c4, wide.c5, wide.c6, wide.c7, wide.c8, wide.c9)

    with pytest.raises(ValueError, match=r"returning_all_from\(relation\)"):
        _call_dynamically(insert_into(wide).values(c1=1).returning, *columns)


def test_returning_all_from_returns_every_declared_column_in_schema_order() -> None:
    database = _database()
    values = {f"c{index}": index for index in range(1, 11)}

    inserted = database.fetch_all(insert_into(wide).values(**values).returning_all_from(wide))

    assert inserted == [tuple(range(1, 11))]
    assert compile_sqlite(delete_from(wide).all_rows().returning_all_from(wide)).sql.endswith(
        'returning "wide"."c1", "wide"."c2", "wide"."c3", "wide"."c4", "wide"."c5", '
        '"wide"."c6", "wide"."c7", "wide"."c8", "wide"."c9", "wide"."c10"'
    )
    assert database.fetch_all(delete_from(wide).all_rows().returning_all_from(wide)) == inserted


def test_returning_is_single_assignment_across_both_forms() -> None:
    query = insert_into(wide).values(c1=1).returning_all_from(wide)

    with pytest.raises(ValueError, match="only be specified once"):
        query.returning_all_from(wide)
