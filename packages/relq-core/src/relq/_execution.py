"""Private executor support: command safety and explicit result mapping."""

from collections.abc import Iterable
from typing import Literal, cast

from relq._ast import SelectNode
from relq._query import Query, extract_query
from relq.dml import (
    DeleteQuery,
    InsertQuery,
    UpdateQuery,
)

type Command[Row] = (
    InsertQuery[Row, Literal[False]]
    | UpdateQuery[Row, Literal[False], Literal[True]]
    | DeleteQuery[Row, Literal[False], Literal[True]]
)


def require_command[Row](query: Query[Row]) -> None:
    """Reject queries that produce rows before an adapter executes them."""
    result = extract_query(query).node
    if isinstance(result, SelectNode) or result.returning:
        raise TypeError("execute() cannot consume RETURNING rows; use fetch_all() or fetch_one()")


def map_all[Row](query: Query[Row], rows: Iterable[tuple[object, ...]]) -> list[Row]:
    """Map rows through a declared adapter, preserving raw driver tuples otherwise."""
    result_adapter = extract_query(query).adapter
    if result_adapter is None:
        return cast(list[Row], list(rows))
    return [result_adapter.map(row) for row in rows]


def map_row[Row](query: Query[Row], row: tuple[object, ...]) -> Row:
    """Map one row through a query-declared adapter, or pass it through raw."""
    result_adapter = extract_query(query).adapter
    if result_adapter is None:
        return cast(Row, row)
    return result_adapter.map(row)


def map_one[Row](query: Query[Row], row: tuple[object, ...] | None) -> Row | None:
    """Map one optional row through a query-declared adapter."""
    if row is None:
        return None
    return map_row(query, row)
