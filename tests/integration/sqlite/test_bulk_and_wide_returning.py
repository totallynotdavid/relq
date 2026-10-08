"""Bulk inserts past the SQLite parameter ceiling and RETURNING without an expression limit."""

import sqlite3
from collections.abc import Callable, Iterator, Mapping
from typing import Literal

import pytest
from relq import (
    Column,
    InsertQuery,
    Table,
    add,
    coalesce,
    column,
    delete_from,
    insert_into,
    select,
    value,
)
from relq._compiler import compile_sqlite
from relq._query import Query
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


def _insert(batch: tuple[Mapping[str, object], ...]) -> InsertQuery[tuple[()], Literal[False]]:
    return insert_into(employees).values_many(batch)


def _upsert(batch: tuple[Mapping[str, object], ...]) -> InsertQuery[tuple[int, int], Literal[True]]:
    """An upsert that binds two parameters in its update and two in RETURNING."""
    return (
        insert_into(employees)
        .values_many(batch)
        .on_conflict(employees.id)
        .do_update(name=coalesce(value("a"), value("b")))
        .returning(add(employees.id, 1), add(employees.id, 2))
    )


def _parameters[Row](statement: Query[Row]) -> int:
    return len(compile_sqlite(statement).parameters)


def test_row_batches_split_by_the_ceiling_and_insert_every_row_once() -> None:
    database = _database()
    rows = [{"id": index, "name": f"n{index}"} for index in range(1_300)]

    statements = list(row_batches(rows, _insert))

    assert [_parameters(statement) for statement in statements] == [998, 998, 604]
    with database.transaction() as transaction:
        inserted = sum(transaction.execute(statement) for statement in statements)
    assert inserted == 1_300
    assert database.fetch_one(select(employees.id).from_(employees).limit(1)) == (0,)


def test_row_batches_of_no_rows_yield_nothing() -> None:
    assert list(row_batches([], _insert)) == []


def test_row_batches_read_a_generator_one_batch_at_a_time() -> None:
    consumed = 0

    def stream() -> Iterator[dict[str, object]]:
        nonlocal consumed
        for index in range(2_000):
            consumed += 1
            yield {"id": index, "name": "x"}

    first = next(row_batches(stream(), _insert))

    assert _parameters(first) == 998
    # The row after a full batch is read to learn that the batch is full.
    assert consumed == 500


def test_row_batches_count_the_parameters_an_expression_cell_binds() -> None:
    database = _database()
    rows = [{"id": add(value(index), 0), "name": f"n{index}"} for index in range(1_000)]

    statements = list(row_batches(rows, _insert))

    # Three parameters per row allow 333 rows to fill 999 parameters.
    # Counting one per cell would allow 499 rows and 1,497 parameters.
    assert [_parameters(statement) for statement in statements] == [999, 999, 999, 3]
    with database.transaction() as transaction:
        inserted = sum(transaction.execute(statement) for statement in statements)
    assert inserted == 1_000


def test_row_batches_fill_each_batch_with_rows_of_different_cost() -> None:
    cheap = {"id": 1, "name": "a"}
    dear = {"id": add(value(1), 0), "name": "a"}
    rows = [dear] * 300 + [cheap] * 200

    statements = list(row_batches(rows, _insert))

    # 300 rows of three parameters and 49 of two fill 998 parameters.
    # The other 151 rows take 302 parameters.
    assert [_parameters(statement) for statement in statements] == [998, 302]


def test_row_batches_count_the_parameters_on_conflict_and_returning_bind() -> None:
    database = _database()
    rows = [{"id": index, "name": f"n{index}"} for index in range(1_000)]

    statements = list(row_batches(rows, _upsert))

    # The upsert binds four parameters besides the rows, so a batch holds 497 rows.
    # Counting rows alone fills a batch with 499 and compiles to 1,002 parameters.
    assert [_parameters(statement) for statement in statements] == [998, 998, 16]
    with database.transaction() as transaction:
        returned = [row for statement in statements for row in transaction.fetch_all(statement)]
    assert returned == [(index + 1, index + 2) for index in range(1_000)]


def test_row_batches_reject_a_row_wider_than_the_ceiling() -> None:
    heavy = coalesce(value(0), value(1), *(value(index) for index in range(2, 1_000)))

    with pytest.raises(ValueError, match="needs 1001 parameters, over the 999-parameter"):
        list(row_batches([{"id": heavy, "name": "a"}], _insert))


def test_row_batches_reject_a_row_that_only_fits_without_the_statement_around_it() -> None:
    nearly_full = coalesce(value(0), value(1), *(value(index) for index in range(2, 997)))
    row = {"id": nearly_full, "name": "a"}
    assert _parameters(_insert((row,))) == 997 + 1

    with pytest.raises(ValueError, match="needs 1002 parameters, over the 999-parameter"):
        list(row_batches([row], _upsert))


def test_row_batches_reject_a_row_with_no_columns() -> None:
    with pytest.raises(ValueError, match="at least one column"):
        list(row_batches([{"id": 1, "name": "a"}, {}], _insert))


def test_row_batches_reject_a_statement_that_does_not_insert_the_batch() -> None:
    def ignores_the_batch(
        batch: tuple[Mapping[str, object], ...],
    ) -> InsertQuery[tuple[()], Literal[False]]:
        return insert_into(employees).values(id=1, name="a")

    with pytest.raises(TypeError, match="exactly the rows it is given"):
        list(row_batches([{"id": 1, "name": "a"}], ignores_the_batch))


def test_a_row_of_the_wrong_shape_is_still_rejected_inside_a_batch() -> None:
    with pytest.raises(ValueError, match="same columns"):
        next(row_batches([{"id": 1, "name": "a"}, {"id": 2}], _insert))


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
