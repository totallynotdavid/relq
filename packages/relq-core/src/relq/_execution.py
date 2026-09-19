"""Private executor support: command safety, result mapping, and observability."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal, NoReturn, Protocol, cast

from relq._ast import SelectNode
from relq._query import Query, extract_query
from relq.dml import (
    DeleteQuery,
    InsertQuery,
    ModelDeleteQuery,
    ModelInsertQuery,
    ModelUpdateQuery,
    UpdateQuery,
)
from relq.query import ModelSelectQuery, SelectQuery

type RawResultQuery[Row] = (
    SelectQuery[Row]
    | InsertQuery[Row, Literal[True]]
    | UpdateQuery[Row, Literal[True], Literal[True]]
    | DeleteQuery[Row, Literal[True], Literal[True]]
)
type MappedResultQuery[Model] = (
    ModelSelectQuery[Model]
    | ModelInsertQuery[Model]
    | ModelUpdateQuery[Model]
    | ModelDeleteQuery[Model]
)
type Command[Row] = (
    InsertQuery[Row, Literal[False]]
    | UpdateQuery[Row, Literal[False], Literal[True]]
    | DeleteQuery[Row, Literal[False], Literal[True]]
)


@dataclass(frozen=True, slots=True)
class QueryEvent:
    """The compiled statement and outcome of one executor operation."""

    sql: str
    parameters: tuple[object, ...]
    duration: float
    row_count: int | None
    error: BaseException | None


class QueryObserver(Protocol):
    """Receive one event for each completed fetch, execute, or stream."""

    def __call__(self, event: QueryEvent, /) -> None: ...


class NoResultError(LookupError):
    """Raised by an ``or_raise`` executor method when no row is returned."""


class TransactionUnavailableError(RuntimeError):
    """Raised when a controlled transaction or savepoint is no longer usable."""


def raise_no_result(error: Callable[[], Exception] | None) -> NoReturn:
    """Raise the default or caller-provided exception for an empty result."""
    if error is None:
        raise NoResultError("query returned no rows")
    raise error()


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
