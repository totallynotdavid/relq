"""Synchronous stdlib SQLite executor for relq."""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator, Sequence
from contextlib import AbstractContextManager, contextmanager
from itertools import count
from time import perf_counter
from types import TracebackType
from typing import NoReturn, Protocol, Self, cast, overload

from relq import RowAdapter
from relq._compiler.api import compile_sqlite
from relq._execution import (
    Command,
    NoResultError,
    QueryEvent,
    QueryObserver,
    ReturningQuery,
    TransactionUnavailableError,
    map_all,
    map_one,
    raise_no_result,
    require_command,
)
from relq._query import Query, extract_query
from relq.query import SelectQuery

__all__ = [
    "ControlledTransaction",
    "NoResultError",
    "QueryEvent",
    "QueryObserver",
    "SQLiteDatabase",
    "Savepoint",
    "TransactionUnavailableError",
]


_LOGGER = logging.getLogger(__name__)


class _Cursor(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...

    def fetchone(self) -> tuple[object, ...] | None: ...


class _Connection(Protocol):
    @property
    def autocommit(self) -> bool | int: ...

    @property
    def in_transaction(self) -> bool: ...

    @property
    def isolation_level(self) -> str | None: ...

    def execute(self, sql: str, parameters: Sequence[object] = (), /) -> _Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> bool | None: ...


class Savepoint(Protocol):
    """A SQLite savepoint belonging to a controlled transaction."""

    def release(self) -> None: ...

    def rollback(self) -> None: ...


class ControlledTransaction(Protocol):
    """A manually controlled SQLite transaction and its normal query methods."""

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def savepoint(self, name: str) -> Savepoint: ...

    def begin(self) -> ControlledTransaction: ...

    def transaction(self) -> AbstractContextManager[SQLiteDatabase]: ...

    @overload
    def fetch_all[SqlRow, Row](self, query: SelectQuery[SqlRow, Row]) -> list[Row]: ...

    @overload
    def fetch_all[Row](self, query: ReturningQuery[Row]) -> list[Row]: ...

    @overload
    def fetch_one[SqlRow, Row](self, query: SelectQuery[SqlRow, Row]) -> Row | None: ...

    @overload
    def fetch_one[Row](self, query: ReturningQuery[Row]) -> Row | None: ...

    @overload
    def fetch_all_as[SqlRow, Row, Model](
        self, query: SelectQuery[SqlRow, Row], adapter: RowAdapter[Model]
    ) -> list[Model]: ...

    @overload
    def fetch_all_as[Row, Model](
        self, query: ReturningQuery[Row], adapter: RowAdapter[Model]
    ) -> list[Model]: ...

    @overload
    def fetch_one_as[SqlRow, Row, Model](
        self, query: SelectQuery[SqlRow, Row], adapter: RowAdapter[Model]
    ) -> Model | None: ...

    @overload
    def fetch_one_as[Row, Model](
        self, query: ReturningQuery[Row], adapter: RowAdapter[Model]
    ) -> Model | None: ...

    @overload
    def fetch_one_or_raise[SqlRow, Row](
        self, query: SelectQuery[SqlRow, Row], *, error: Callable[[], Exception] | None = None
    ) -> Row: ...

    @overload
    def fetch_one_or_raise[Row](
        self, query: ReturningQuery[Row], *, error: Callable[[], Exception] | None = None
    ) -> Row: ...

    @overload
    def fetch_one_as_or_raise[SqlRow, Row, Model](
        self,
        query: SelectQuery[SqlRow, Row],
        adapter: RowAdapter[Model],
        *,
        error: Callable[[], Exception] | None = None,
    ) -> Model: ...

    @overload
    def fetch_one_as_or_raise[Row, Model](
        self,
        query: ReturningQuery[Row],
        adapter: RowAdapter[Model],
        *,
        error: Callable[[], Exception] | None = None,
    ) -> Model: ...

    def execute[Row](self, query: Command[Row]) -> int: ...


# This process-wide sequence makes every generated controlled-transaction
# savepoint name unique across all concurrently open transactions on a
# connection, including while multiple connections have open transactions.
_TRANSACTION_SEQUENCE = count(1)


class _SQLiteRegistryEntry(Protocol):
    def _invalidate(self) -> None: ...


class _SQLiteTransactionState:
    def __init__(self) -> None:
        self.stack: list[_SQLiteControlledTransaction] = []
        self.entries: list[_SQLiteRegistryEntry] = []
        self.savepoint_names: set[str] = set()

    def register_transaction(self, transaction: _SQLiteControlledTransaction) -> None:
        self.stack.append(transaction)
        self.entries.append(transaction)

    def register_savepoint(self, savepoint: _SQLiteSavepoint) -> None:
        self.entries.append(savepoint)

    def remove_entry(self, entry: _SQLiteRegistryEntry) -> None:
        if entry in self.entries:
            self.entries.remove(entry)

    def invalidate_after(self, entry: _SQLiteRegistryEntry) -> None:
        try:
            index = self.entries.index(entry)
        except ValueError:
            return
        for descendant in tuple(self.entries[index + 1 :]):
            descendant._invalidate()  # pyright: ignore[reportPrivateUsage]

    def reserve_savepoint_name(self, name: str) -> None:
        key = _savepoint_name_key(name)
        if key in self.savepoint_names:
            raise ValueError(f"savepoint name is already active: {name!r}")
        self.savepoint_names.add(key)

    def release_savepoint_name(self, name: str) -> None:
        self.savepoint_names.discard(_savepoint_name_key(name))

    def invalidate_all(self) -> None:
        for entry in tuple(self.entries):
            entry._invalidate()  # pyright: ignore[reportPrivateUsage]
        self.entries.clear()
        self.stack.clear()
        self.savepoint_names.clear()


# All database wrappers for one physical connection resolve to this state;
# entries are removed once the registry is empty so a later transaction gets
# a fresh state after the previous boundary has fully closed.
_TRANSACTION_STATES_BY_CONNECTION: dict[int, _SQLiteTransactionState] = {}


class SQLiteDatabase:
    """Own a sqlite3 connection and execute typed relq SELECT statements."""

    def __init__(self, connection: _Connection, *, observer: QueryObserver | None = None) -> None:
        self._connection = connection
        self._observer = observer
        self._transaction_state = _transaction_state_for_connection(connection)

    def _ensure_usable(self) -> None:
        """Allow controlled subclasses to reject use after completion."""

    def _observe(
        self,
        compiled_sql: str,
        parameters: tuple[object, ...],
        started: float,
        row_count: int | None,
        error: BaseException | None,
    ) -> None:
        if self._observer is None:
            return
        event = QueryEvent(
            compiled_sql,
            parameters,
            perf_counter() - started,
            row_count,
            error,
        )
        try:
            self._observer(event)
        except Exception:
            _LOGGER.exception("Query observer raised while handling an execution event")

    def _run_control(self, sql: str, operation: Callable[[], object]) -> float:
        started = perf_counter()
        try:
            operation()
        except BaseException as error:
            self._observe(sql, (), started, None, error)
            raise
        return started

    @overload
    def fetch_all[SqlRow, Row](self, query: SelectQuery[SqlRow, Row]) -> list[Row]: ...

    @overload
    def fetch_all[Row](self, query: ReturningQuery[Row]) -> list[Row]: ...

    def fetch_all[Row](self, query: Query[Row]) -> list[Row]:
        self._ensure_usable()
        compiled = compile_sqlite(query)
        started = perf_counter()
        try:
            cursor = self._connection.execute(compiled.sql, compiled.parameters)
            rows = cursor.fetchall()
            result = map_all(query, rows)
        except BaseException as error:
            self._observe(compiled.sql, compiled.parameters, started, None, error)
            raise
        self._observe(compiled.sql, compiled.parameters, started, len(rows), None)
        return result

    @overload
    def fetch_one[SqlRow, Row](self, query: SelectQuery[SqlRow, Row]) -> Row | None: ...

    @overload
    def fetch_one[Row](self, query: ReturningQuery[Row]) -> Row | None: ...

    def fetch_one[Row](self, query: Query[Row]) -> Row | None:
        self._ensure_usable()
        compiled = compile_sqlite(query)
        started = perf_counter()
        try:
            cursor = self._connection.execute(compiled.sql, compiled.parameters)
            row = cursor.fetchone()
            result = map_one(query, row)
        except BaseException as error:
            self._observe(compiled.sql, compiled.parameters, started, None, error)
            raise
        self._observe(compiled.sql, compiled.parameters, started, 0 if row is None else 1, None)
        return result

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
        self._ensure_usable()
        if extract_query(query).adapter is not None:
            raise TypeError("query already declares a result model; use fetch_all()")
        compiled = compile_sqlite(query)
        started = perf_counter()
        try:
            cursor = self._connection.execute(compiled.sql, compiled.parameters)
            rows = cursor.fetchall()
            result = [adapter.map(row) for row in rows]
        except BaseException as error:
            self._observe(compiled.sql, compiled.parameters, started, None, error)
            raise
        self._observe(compiled.sql, compiled.parameters, started, len(rows), None)
        return result

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
        self._ensure_usable()
        if extract_query(query).adapter is not None:
            raise TypeError("query already declares a result model; use fetch_one()")
        compiled = compile_sqlite(query)
        started = perf_counter()
        try:
            row = self._connection.execute(compiled.sql, compiled.parameters).fetchone()
            result = None if row is None else adapter.map(row)
        except BaseException as error:
            self._observe(compiled.sql, compiled.parameters, started, None, error)
            raise
        self._observe(compiled.sql, compiled.parameters, started, 0 if row is None else 1, None)
        return result

    @overload
    def fetch_one_or_raise[SqlRow, Row](
        self, query: SelectQuery[SqlRow, Row], *, error: Callable[[], Exception] | None = None
    ) -> Row: ...

    @overload
    def fetch_one_or_raise[Row](
        self, query: ReturningQuery[Row], *, error: Callable[[], Exception] | None = None
    ) -> Row: ...

    def fetch_one_or_raise(
        self,
        query: SelectQuery[object, object] | ReturningQuery[object],
        *,
        error: Callable[[], Exception] | None = None,
    ) -> object:
        result = self.fetch_one(query)
        if result is None:
            raise_no_result(error)
        return result

    @overload
    def fetch_one_as_or_raise[SqlRow, Row, Model](
        self,
        query: SelectQuery[SqlRow, Row],
        adapter: RowAdapter[Model],
        *,
        error: Callable[[], Exception] | None = None,
    ) -> Model: ...

    @overload
    def fetch_one_as_or_raise[Row, Model](
        self,
        query: ReturningQuery[Row],
        adapter: RowAdapter[Model],
        *,
        error: Callable[[], Exception] | None = None,
    ) -> Model: ...

    def fetch_one_as_or_raise(
        self,
        query: SelectQuery[object, object] | ReturningQuery[object],
        adapter: RowAdapter[object],
        *,
        error: Callable[[], Exception] | None = None,
    ) -> object:
        result = self.fetch_one_as(query, adapter)
        if result is None:
            raise_no_result(error)
        return result

    def execute[Row](self, query: Command[Row]) -> int:
        self._ensure_usable()
        require_command(query)
        compiled = compile_sqlite(query)
        started = perf_counter()
        try:
            count = self._connection.execute(compiled.sql, compiled.parameters).rowcount
        except BaseException as error:
            self._observe(compiled.sql, compiled.parameters, started, None, error)
            raise
        self._observe(compiled.sql, compiled.parameters, started, count, None)
        return count

    def begin(self) -> ControlledTransaction:
        self._ensure_usable()
        self._transaction_state = _transaction_state_for_connection(self._connection)
        if self._transaction_state.stack:
            return self._begin_nested()
        return self._begin_top_level()

    def _begin_nested(self) -> ControlledTransaction:
        name = _quote_savepoint(f"__relq_controlled_{next(_TRANSACTION_SEQUENCE)}")
        self._transaction_state.reserve_savepoint_name(name)
        sql = f"savepoint {name}"
        try:
            started = self._run_control(sql, lambda: self._connection.execute(sql))
        except BaseException:
            self._transaction_state.release_savepoint_name(name)
            raise
        try:
            self._observe(sql, (), started, 0, None)
        except BaseException:
            self._transaction_state.release_savepoint_name(name)
            self._cleanup_interrupted_savepoint(name)
            raise
        return _SQLiteControlledTransaction(
            self._connection,
            observer=self._observer,
            generated_savepoint_name=name,
            transaction_state=self._transaction_state,
        )

    def _begin_top_level(self) -> ControlledTransaction:
        if not self._connection.in_transaction:
            sql = _begin_statement(self._connection.isolation_level)
            try:
                started = self._run_control(sql, lambda: self._connection.execute(sql))
                self._observe(sql, (), started, 0, None)
            except BaseException:
                self._cleanup_interrupted_transaction()
                raise
        return _SQLiteControlledTransaction(
            self._connection,
            observer=self._observer,
            transaction_state=self._transaction_state,
        )

    def _cleanup_interrupted_savepoint(self, name: str) -> None:
        try:
            self._connection.execute(f"rollback to savepoint {name}")
            self._connection.execute(f"release savepoint {name}")
        except BaseException:
            _LOGGER.debug("failed to clean up an observer-interrupted savepoint", exc_info=True)

    def _cleanup_interrupted_transaction(self) -> None:
        try:
            self._connection.execute("rollback")
        except BaseException:
            _LOGGER.debug("failed to clean up an observer-interrupted transaction", exc_info=True)

    @contextmanager
    def transaction(self) -> Generator[SQLiteDatabase]:
        controlled = self.begin()
        try:
            yield cast(SQLiteDatabase, controlled)
        except BaseException:
            try:
                controlled.rollback()
            except TransactionUnavailableError:
                pass
            raise
        else:
            controlled.commit()


class _SQLiteControlledTransaction(SQLiteDatabase):
    def __init__(
        self,
        connection: _Connection,
        *,
        observer: QueryObserver | None,
        transaction_state: _SQLiteTransactionState,
        generated_savepoint_name: str | None = None,
    ) -> None:
        super().__init__(connection, observer=observer)
        self._transaction_state = transaction_state
        self._savepoint_names: set[str] = set()
        self._savepoints: list[_SQLiteSavepoint] = []
        self._generated_savepoint_name = generated_savepoint_name
        if generated_savepoint_name is not None:
            self._savepoint_names.add(generated_savepoint_name)
        self._closed = False
        self._transaction_state.register_transaction(self)

    def _ensure_usable(self) -> None:
        if self._closed:
            raise TransactionUnavailableError("controlled transaction is no longer usable")

    @property
    def raw_connection(self) -> _Connection:
        return self._connection

    def _close(self) -> None:
        self._clear_savepoint_names()
        self._closed = True
        self._transaction_state.remove_entry(self)
        if self in self._transaction_state.stack:
            self._transaction_state.stack.remove(self)
        _discard_transaction_state(self._connection, self._transaction_state)

    def _invalidate(self) -> None:
        self._close()

    def _clear_savepoint_names(self) -> None:
        for savepoint in tuple(self._savepoints):
            savepoint._invalidate()  # pyright: ignore[reportPrivateUsage]
        self._savepoints.clear()
        for name in self._savepoint_names:
            self._transaction_state.release_savepoint_name(name)
        self._savepoint_names.clear()

    def _forget_savepoint(self, savepoint: _SQLiteSavepoint) -> None:
        if savepoint in self._savepoints:
            self._savepoints.remove(savepoint)
        self._transaction_state.remove_entry(savepoint)
        name = savepoint.name
        self._savepoint_names.discard(name)
        self._transaction_state.release_savepoint_name(name)

    def _invalidate_descendant_savepoints(self, savepoint: _SQLiteSavepoint) -> None:
        self._transaction_state.invalidate_after(savepoint)

    def _release_savepoint(self, savepoint: _SQLiteSavepoint) -> None:
        self._transaction_state.invalidate_after(savepoint)
        self._forget_savepoint(savepoint)

    def commit(self) -> None:
        self._ensure_usable()
        self._transaction_state.invalidate_after(self)
        sql = (
            "commit"
            if self._generated_savepoint_name is None
            else f"release savepoint {self._generated_savepoint_name}"
        )
        try:
            operation = (
                self._top_level_control(sql)
                if self._generated_savepoint_name is None
                else lambda: self._connection.execute(sql)
            )
            started = self._run_control(sql, operation)
        except BaseException as commit_error:  # noqa: BLE001
            self._recover_after_control_failure(commit_error)
        else:
            self._close()
            self._observe(sql, (), started, 0, None)

    def rollback(self) -> None:
        self._ensure_usable()
        self._transaction_state.invalidate_after(self)
        if self._generated_savepoint_name is None:
            sql = "rollback"
            try:
                started = self._run_control(sql, self._top_level_control(sql))
            except BaseException as rollback_error:  # noqa: BLE001
                self._recover_after_control_failure(rollback_error)
            else:
                self._close()
                self._observe(sql, (), started, 0, None)
            return

        rollback_sql = f"rollback to savepoint {self._generated_savepoint_name}"
        release_sql = f"release savepoint {self._generated_savepoint_name}"
        observer_error: BaseException | None = None
        try:
            rollback_started = self._run_control(
                rollback_sql, lambda: self._connection.execute(rollback_sql)
            )
        except BaseException as rollback_error:  # noqa: BLE001
            self._recover_after_control_failure(rollback_error)
        try:
            self._observe(rollback_sql, (), rollback_started, 0, None)
        except BaseException as error:  # noqa: BLE001
            observer_error = error
        try:
            release_started = self._run_control(
                release_sql, lambda: self._connection.execute(release_sql)
            )
        except BaseException as release_error:  # noqa: BLE001
            self._recover_after_control_failure(release_error)
        else:
            self._close()
            try:
                self._observe(release_sql, (), release_started, 0, None)
            except BaseException as error:  # noqa: BLE001
                if observer_error is None:
                    observer_error = error
            if observer_error is not None:
                raise observer_error

    def _top_level_control(self, sql: str) -> Callable[[], object]:
        if self._connection.autocommit is True:
            return lambda: self._connection.execute(sql)
        if sql == "commit":
            return self._connection.commit
        return self._connection.rollback

    def _recover_after_control_failure(self, operation_error: BaseException) -> NoReturn:
        self._transaction_state.invalidate_all()
        sql = "rollback"
        try:
            started = self._run_control(sql, lambda: self._connection.execute(sql))
            self._observe(sql, (), started, 0, None)
        except BaseException as recovery_error:
            raise recovery_error from operation_error
        raise operation_error

    def savepoint(self, name: str) -> Savepoint:
        self._ensure_usable()
        quoted = _quote_savepoint(name)
        self._transaction_state.reserve_savepoint_name(quoted)
        sql = f"savepoint {quoted}"
        try:
            started = self._run_control(sql, lambda: self._connection.execute(sql))
        except BaseException:
            self._transaction_state.release_savepoint_name(quoted)
            raise
        self._savepoint_names.add(quoted)
        savepoint = _SQLiteSavepoint(self, quoted)
        self._savepoints.append(savepoint)
        self._transaction_state.register_savepoint(savepoint)
        try:
            self._observe(sql, (), started, 0, None)
        except BaseException:
            savepoint._invalidate()  # pyright: ignore[reportPrivateUsage]
            try:
                self._connection.execute(f"release savepoint {quoted}")
            except BaseException:
                _LOGGER.debug("failed to clean up an observer-interrupted savepoint", exc_info=True)
            raise
        return savepoint


class _SQLiteSavepoint:
    def __init__(self, transaction: _SQLiteControlledTransaction, quoted_name: str) -> None:
        self._transaction = transaction
        self._name = quoted_name
        self._released = False
        self._invalidated = False

    @property
    def name(self) -> str:
        return self._name

    def _invalidate(self) -> None:
        if self._released or self._invalidated:
            return
        self._transaction._forget_savepoint(self)  # pyright: ignore[reportPrivateUsage]
        self._invalidated = True

    def _ensure_usable(self) -> None:
        self._transaction._ensure_usable()  # pyright: ignore[reportPrivateUsage]
        if self._released or self._invalidated:
            raise TransactionUnavailableError("savepoint is no longer usable")

    def release(self) -> None:
        self._ensure_usable()
        sql = f"release savepoint {self._name}"
        try:
            started = self._transaction._run_control(  # pyright: ignore[reportPrivateUsage]
                sql, lambda: self._transaction.raw_connection.execute(sql)
            )
        except BaseException as error:  # noqa: BLE001
            self._transaction._invalidate_descendant_savepoints(self)  # pyright: ignore[reportPrivateUsage]
            self._invalidate()
            self._transaction._recover_after_control_failure(  # pyright: ignore[reportPrivateUsage]
                error
            )
        else:
            self._transaction._release_savepoint(self)  # pyright: ignore[reportPrivateUsage]
            self._released = True
            self._transaction._observe(sql, (), started, 0, None)  # pyright: ignore[reportPrivateUsage]

    def rollback(self) -> None:
        self._ensure_usable()
        sql = f"rollback to savepoint {self._name}"
        try:
            started = self._transaction._run_control(  # pyright: ignore[reportPrivateUsage]
                sql, lambda: self._transaction.raw_connection.execute(sql)
            )
        except BaseException as error:  # noqa: BLE001
            self._transaction._invalidate_descendant_savepoints(self)  # pyright: ignore[reportPrivateUsage]
            self._invalidate()
            self._transaction._recover_after_control_failure(  # pyright: ignore[reportPrivateUsage]
                error
            )
        else:
            self._transaction._invalidate_descendant_savepoints(self)  # pyright: ignore[reportPrivateUsage]
            self._transaction._observe(sql, (), started, 0, None)  # pyright: ignore[reportPrivateUsage]


def _quote_savepoint(name: str) -> str:
    if not name:
        raise ValueError("savepoint name must not be empty")
    return '"' + name.replace('"', '""') + '"'


def _savepoint_name_key(name: str) -> str:
    return "".join(
        chr(ord(character) + (ord("a") - ord("A"))) if "A" <= character <= "Z" else character
        for character in name
    )


def _begin_statement(isolation_level: str | None) -> str:
    if not isolation_level:
        return "begin"
    mode = isolation_level.upper()
    if mode not in {"DEFERRED", "IMMEDIATE", "EXCLUSIVE"}:
        raise ValueError(f"unsupported SQLite isolation level: {isolation_level!r}")
    return f"begin {mode.lower()}"


def _transaction_state_for_connection(connection: _Connection) -> _SQLiteTransactionState:
    key = id(connection)
    state = _TRANSACTION_STATES_BY_CONNECTION.get(key)
    if state is None:
        state = _SQLiteTransactionState()
        _TRANSACTION_STATES_BY_CONNECTION[key] = state
    return state


def _discard_transaction_state(connection: _Connection, state: _SQLiteTransactionState) -> None:
    key = id(connection)
    if not state.entries and _TRANSACTION_STATES_BY_CONNECTION.get(key) is state:
        _TRANSACTION_STATES_BY_CONNECTION.pop(key, None)
