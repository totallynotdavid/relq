"""Synchronous stdlib SQLite executor for relq."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from types import TracebackType
from typing import Protocol, Self, overload

from relq import RowAdapter
from relq._compiler.api import compile_sqlite
from relq._execution import (
    Command,
    ReturningQuery,
    map_all,
    map_one,
    require_command,
)
from relq._query import Query, extract_query
from relq.query import SelectQuery


class _Cursor(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...

    def fetchone(self) -> tuple[object, ...] | None: ...


class _Connection(Protocol):
    def execute(self, sql: str, parameters: Sequence[object] = (), /) -> _Cursor: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> bool | None: ...


class SQLiteDatabase:
    """Own a sqlite3 connection and execute typed relq SELECT statements."""

    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    @overload
    def fetch_all[SqlRow, Row](self, query: SelectQuery[SqlRow, Row]) -> list[Row]: ...

    @overload
    def fetch_all[Row](self, query: ReturningQuery[Row]) -> list[Row]: ...

    def fetch_all[Row](self, query: Query[Row]) -> list[Row]:
        compiled = compile_sqlite(query)
        cursor = self._connection.execute(compiled.sql, compiled.parameters)
        return map_all(query, cursor.fetchall())

    @overload
    def fetch_one[SqlRow, Row](self, query: SelectQuery[SqlRow, Row]) -> Row | None: ...

    @overload
    def fetch_one[Row](self, query: ReturningQuery[Row]) -> Row | None: ...

    def fetch_one[Row](self, query: Query[Row]) -> Row | None:
        compiled = compile_sqlite(query)
        cursor = self._connection.execute(compiled.sql, compiled.parameters)
        return map_one(query, cursor.fetchone())

    @overload
    def fetch_all_as[SqlRow, Row, Model](
        self, query: SelectQuery[SqlRow, Row], adapter: RowAdapter[Model]
    ) -> list[Model]: ...

    @overload
    def fetch_all_as[Row, Model](
        self, query: ReturningQuery[Row], adapter: RowAdapter[Model]
    ) -> list[Model]: ...

    def fetch_all_as[Row, Model](
        self, query: Query[Row], adapter: RowAdapter[Model]
    ) -> list[Model]:
        """Map result rows through an explicit, arity-validating adapter."""
        if extract_query(query).adapter is not None:
            raise TypeError("query already declares a result model; use fetch_all()")
        compiled = compile_sqlite(query)
        cursor = self._connection.execute(compiled.sql, compiled.parameters)
        return [adapter.map(row) for row in cursor.fetchall()]

    @overload
    def fetch_one_as[SqlRow, Row, Model](
        self, query: SelectQuery[SqlRow, Row], adapter: RowAdapter[Model]
    ) -> Model | None: ...

    @overload
    def fetch_one_as[Row, Model](
        self, query: ReturningQuery[Row], adapter: RowAdapter[Model]
    ) -> Model | None: ...

    def fetch_one_as[Row, Model](
        self, query: Query[Row], adapter: RowAdapter[Model]
    ) -> Model | None:
        if extract_query(query).adapter is not None:
            raise TypeError("query already declares a result model; use fetch_one()")
        compiled = compile_sqlite(query)
        row = self._connection.execute(compiled.sql, compiled.parameters).fetchone()
        return None if row is None else adapter.map(row)

    def execute[Row](self, query: Command[Row]) -> int:
        require_command(query)
        compiled = compile_sqlite(query)
        return self._connection.execute(compiled.sql, compiled.parameters).rowcount

    @contextmanager
    def transaction(self) -> Generator[SQLiteDatabase]:
        with self._connection:
            yield self
