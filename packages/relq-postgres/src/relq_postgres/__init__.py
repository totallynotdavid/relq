"""Asynchronous asyncpg executor for relq."""

from __future__ import annotations

import logging
import weakref
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Literal, NoReturn, Protocol, cast, overload

import asyncpg
from relq import Interval, RowAdapter
from relq._compiler.api import compile_postgres
from relq._execution import (
    Command,
    NoResultError,
    QueryEvent,
    QueryObserver,
    ReturningQuery,
    TransactionUnavailableError,
    map_all,
    map_one,
    map_row,
    raise_no_result,
    require_command,
)
from relq._query import Query, extract_query
from relq.query import SelectQuery

__all__ = [
    "Connection",
    "ControlledTransaction",
    "NoResultError",
    "PostgresDatabase",
    "QueryEvent",
    "QueryObserver",
    "Savepoint",
    "TransactionUnavailableError",
]


_LOGGER = logging.getLogger(__name__)


type Connection = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy[asyncpg.Record]


class _Transaction(Protocol):
    async def start(self) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


@dataclass(slots=True)
class _StreamOutcome:
    count: int = 0
    error: BaseException | None = None
    consumer_error: bool = False

    def record_row(self) -> None:
        self.count += 1

    def record_error(self, error: BaseException) -> None:
        self.error = error


class _AsyncpgConnectionInternals(Protocol):
    _top_xact: object | None


class _AsyncpgTransactionInternals(Protocol):
    _id: str | None


class _PostgresRegistryEntry(Protocol):
    def _invalidate(self) -> None: ...


class _PostgresTransactionState:
    def __init__(self) -> None:
        self.stack: list[ControlledTransaction] = []
        self.entries: list[_PostgresRegistryEntry] = []
        self.savepoint_names: set[str] = set()

    def register_transaction(self, transaction: ControlledTransaction) -> None:
        self.stack.append(transaction)
        self.entries.append(transaction)

    def register_savepoint(self, savepoint: _PostgresSavepoint) -> None:
        self.entries.append(savepoint)

    def remove_transaction(self, transaction: ControlledTransaction) -> None:
        if transaction in self.stack:
            self.stack.remove(transaction)
        self.remove_entry(transaction)

    def remove_entry(self, entry: _PostgresRegistryEntry) -> None:
        if entry in self.entries:
            self.entries.remove(entry)

    def invalidate_after(self, entry: _PostgresRegistryEntry) -> None:
        try:
            index = self.entries.index(entry)
        except ValueError:
            return
        for descendant in tuple(self.entries[index + 1 :]):
            descendant._invalidate()  # pyright: ignore[reportPrivateUsage]

    def invalidate_all(self) -> None:
        for entry in tuple(self.entries):
            entry._invalidate()  # pyright: ignore[reportPrivateUsage]
        self.entries.clear()
        self.stack.clear()
        self.savepoint_names.clear()

    def reserve_savepoint_name(self, name: str) -> None:
        key = _savepoint_name_key(name)
        if key in self.savepoint_names:
            raise ValueError(f"savepoint name is already active: {name!r}")
        self.savepoint_names.add(key)

    def release_savepoint_name(self, name: str) -> None:
        self.savepoint_names.discard(_savepoint_name_key(name))


# Every database wrapper for one physical connection shares one state. The entry
# is dropped once its registry is empty, so the next transaction starts fresh.
_TRANSACTION_STATES_BY_CONNECTION: dict[int, _PostgresTransactionState] = {}


# Controlled transactions that are constructed and not yet closed, per connection
# key. ``ControlledTransaction.__init__`` is the only place that adds one and
# ``_close()`` is the only place that removes one. Recovery only reads this map.
# It snapshots the handles before invalidating them, so the snapshot still
# releases pooled connections if ``_close()`` drops a handle meanwhile. A
# recovery caller can pass a just-closed handle through ``extra`` without
# changing the map.
_TRANSACTIONS_BY_CONNECTION: dict[int, weakref.WeakSet[ControlledTransaction]] = {}


class Savepoint(Protocol):
    """An asyncpg savepoint belonging to a controlled transaction."""

    async def release(self) -> None: ...

    async def rollback(self) -> None: ...


class PostgresDatabase:
    """Execute relq queries through an existing asyncpg pool or connection.

    The caller owns the pool or connection lifecycle. A transaction on a pool
    acquires one connection for its whole scope. A transaction on a direct
    connection reuses that connection. Behind pgbouncer in ``transaction`` or
    ``statement`` pooling mode, pass ``statement_cache_size=0`` to
    ``asyncpg.connect`` or ``create_pool``, because asyncpg's automatic
    prepared-statement cache does not survive that mode.
    """

    def __init__(
        self,
        connection: asyncpg.Connection
        | asyncpg.Pool
        | asyncpg.pool.PoolConnectionProxy[asyncpg.Record],
        *,
        observer: QueryObserver | None = None,
    ) -> None:
        self._connection: asyncpg.Connection | asyncpg.Pool | Connection = connection
        self._observer = observer
        if isinstance(connection, asyncpg.Pool):
            self._transaction_state = _PostgresTransactionState()
        else:
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

    async def _run_control(self, sql: str, operation: Callable[[], Awaitable[object]]) -> float:
        started = perf_counter()
        try:
            await operation()
        except BaseException as error:
            self._observe(sql, (), started, None, error)
            raise
        return started

    async def _run_recovery_control(self, connection: Connection, sql: str) -> None:
        started = await self._run_control(sql, lambda: connection.execute(sql))
        self._observe(sql, (), started, 0, None)

    @staticmethod
    async def _configure_connection(connection: Connection) -> None:
        await connection.set_type_codec(
            "interval",
            schema="pg_catalog",
            encoder=_encode_interval,
            decoder=_decode_interval,
            format="tuple",
        )

    @asynccontextmanager
    async def _connection_scope(self) -> AsyncGenerator[Connection]:
        """Acquire and configure exactly one physical connection per operation."""
        if isinstance(self._connection, asyncpg.Pool):
            async with self._connection.acquire() as connection:
                await self._configure_connection(connection)
                yield connection
            return
        await self._configure_connection(self._connection)
        yield self._connection

    @overload
    async def fetch_all[SqlRow, Row](self, query: SelectQuery[SqlRow, Row]) -> list[Row]: ...

    @overload
    async def fetch_all[Row](self, query: ReturningQuery[Row]) -> list[Row]: ...

    async def fetch_all[Row](self, query: Query[Row]) -> list[Row]:
        self._ensure_usable()
        compiled = compile_postgres(query)
        started = perf_counter()
        try:
            async with self._connection_scope() as connection:
                records = await connection.fetch(compiled.sql, *compiled.parameters)
            rows = [tuple(record) for record in records]
            result = map_all(query, rows)
        except BaseException as error:
            self._observe(compiled.sql, compiled.parameters, started, None, error)
            raise
        self._observe(compiled.sql, compiled.parameters, started, len(rows), None)
        return result

    @overload
    async def fetch_one[SqlRow, Row](self, query: SelectQuery[SqlRow, Row]) -> Row | None: ...

    @overload
    async def fetch_one[Row](self, query: ReturningQuery[Row]) -> Row | None: ...

    async def fetch_one[Row](self, query: Query[Row]) -> Row | None:
        self._ensure_usable()
        compiled = compile_postgres(query)
        started = perf_counter()
        try:
            async with self._connection_scope() as connection:
                record = await connection.fetchrow(compiled.sql, *compiled.parameters)
            row = None if record is None else tuple(record)
            result = map_one(query, row)
        except BaseException as error:
            self._observe(compiled.sql, compiled.parameters, started, None, error)
            raise
        self._observe(compiled.sql, compiled.parameters, started, 0 if row is None else 1, None)
        return result

    @overload
    async def fetch_all_as[SqlRow, Row, Model](
        self, query: SelectQuery[SqlRow, Row], adapter: RowAdapter[Model]
    ) -> list[Model]: ...

    @overload
    async def fetch_all_as[Row, Model](
        self, query: ReturningQuery[Row], adapter: RowAdapter[Model]
    ) -> list[Model]: ...

    async def fetch_all_as[Row, Model](
        self, query: Query[Row], adapter: RowAdapter[Model]
    ) -> list[Model]:
        self._ensure_usable()
        if extract_query(query).adapter is not None:
            raise TypeError("query already declares a result model; use fetch_all()")
        compiled = compile_postgres(query)
        started = perf_counter()
        try:
            async with self._connection_scope() as connection:
                records = await connection.fetch(compiled.sql, *compiled.parameters)
            rows = [tuple(record) for record in records]
            result = [adapter.map(row) for row in rows]
        except BaseException as error:
            self._observe(compiled.sql, compiled.parameters, started, None, error)
            raise
        self._observe(compiled.sql, compiled.parameters, started, len(rows), None)
        return result

    @overload
    async def fetch_one_as[SqlRow, Row, Model](
        self, query: SelectQuery[SqlRow, Row], adapter: RowAdapter[Model]
    ) -> Model | None: ...

    @overload
    async def fetch_one_as[Row, Model](
        self, query: ReturningQuery[Row], adapter: RowAdapter[Model]
    ) -> Model | None: ...

    async def fetch_one_as[Row, Model](
        self, query: Query[Row], adapter: RowAdapter[Model]
    ) -> Model | None:
        self._ensure_usable()
        if extract_query(query).adapter is not None:
            raise TypeError("query already declares a result model; use fetch_one()")
        compiled = compile_postgres(query)
        started = perf_counter()
        try:
            async with self._connection_scope() as connection:
                record = await connection.fetchrow(compiled.sql, *compiled.parameters)
            result = None if record is None else adapter.map(tuple(record))
        except BaseException as error:
            self._observe(compiled.sql, compiled.parameters, started, None, error)
            raise
        self._observe(compiled.sql, compiled.parameters, started, 0 if record is None else 1, None)
        return result

    @overload
    async def fetch_one_or_raise[SqlRow, Row](
        self, query: SelectQuery[SqlRow, Row], *, error: Callable[[], Exception] | None = None
    ) -> Row: ...

    @overload
    async def fetch_one_or_raise[Row](
        self, query: ReturningQuery[Row], *, error: Callable[[], Exception] | None = None
    ) -> Row: ...

    async def fetch_one_or_raise(
        self,
        query: SelectQuery[object, object] | ReturningQuery[object],
        *,
        error: Callable[[], Exception] | None = None,
    ) -> object:
        result = await self.fetch_one(query)
        if result is None:
            raise_no_result(error)
        return result

    @overload
    async def fetch_one_as_or_raise[SqlRow, Row, Model](
        self,
        query: SelectQuery[SqlRow, Row],
        adapter: RowAdapter[Model],
        *,
        error: Callable[[], Exception] | None = None,
    ) -> Model: ...

    @overload
    async def fetch_one_as_or_raise[Row, Model](
        self,
        query: ReturningQuery[Row],
        adapter: RowAdapter[Model],
        *,
        error: Callable[[], Exception] | None = None,
    ) -> Model: ...

    async def fetch_one_as_or_raise(
        self,
        query: SelectQuery[object, object] | ReturningQuery[object],
        adapter: RowAdapter[object],
        *,
        error: Callable[[], Exception] | None = None,
    ) -> object:
        result = await self.fetch_one_as(query, adapter)
        if result is None:
            raise_no_result(error)
        return result

    @overload
    def fetch_iter[SqlRow, Row](
        self, query: SelectQuery[SqlRow, Row]
    ) -> AbstractAsyncContextManager[AsyncIterator[Row]]: ...

    @overload
    def fetch_iter[Row](
        self, query: ReturningQuery[Row]
    ) -> AbstractAsyncContextManager[AsyncIterator[Row]]: ...

    @asynccontextmanager
    async def fetch_iter[Row](self, query: Query[Row]) -> AsyncGenerator[AsyncIterator[Row]]:
        """Stream rows through a server-side cursor.

        A PostgreSQL cursor is only valid inside a transaction. This opens one for
        the whole iteration, as a savepoint when a relq transaction is already
        open. On a pool it also holds one acquired connection for that scope.
        """
        self._ensure_usable()
        compiled = compile_postgres(query)
        started = perf_counter()
        outcome = _StreamOutcome()

        transaction: ControlledTransaction | None = None
        try:
            transaction = await self.begin()
            try:
                try:
                    await self._configure_connection(transaction.raw_connection)
                except BaseException as error:
                    outcome.record_error(error)
                    raise
                yield _stream_rows(
                    query,
                    transaction.raw_connection,
                    compiled.sql,
                    compiled.parameters,
                    outcome.record_row,
                    outcome.record_error,
                )
            except BaseException:
                consumer_exception = outcome.error is None
                try:
                    await transaction.rollback()
                except BaseException as rollback_error:
                    outcome.error = rollback_error
                    raise
                else:
                    outcome.consumer_error = consumer_exception
                raise
            else:
                await transaction.commit()
        except BaseException as error:
            if not outcome.consumer_error:
                outcome.error = error
            raise
        finally:
            self._observe(
                compiled.sql,
                compiled.parameters,
                started,
                None if outcome.error is not None else outcome.count,
                outcome.error,
            )

    async def execute[Row](self, query: Command[Row]) -> int:
        self._ensure_usable()
        require_command(query)
        compiled = compile_postgres(query)
        started = perf_counter()
        try:
            # With bound parameters, execute() shares asyncpg's prepared-statement
            # cache with fetch() and fetchrow(). Only a call without parameters,
            # such as DEFAULT VALUES, bypasses it through the simple-query protocol.
            async with self._connection_scope() as connection:
                status = await connection.execute(compiled.sql, *compiled.parameters)
            count = _command_count(status)
        except BaseException as error:
            self._observe(compiled.sql, compiled.parameters, started, None, error)
            raise
        self._observe(compiled.sql, compiled.parameters, started, count, None)
        return count

    async def begin(self) -> ControlledTransaction:
        self._ensure_usable()
        if isinstance(self._connection, asyncpg.Pool):
            return await self._begin_controlled_transaction(None)
        else:
            transaction_state = _transaction_state_for_connection(self._connection)
            self._transaction_state = transaction_state
        return await self._begin_controlled_transaction(transaction_state)

    async def _begin_controlled_transaction(
        self,
        transaction_state: _PostgresTransactionState | None,
    ) -> ControlledTransaction:
        if isinstance(self._connection, asyncpg.Pool):
            pool = self._connection
            pool_connection = await pool.acquire()
            connection = pool_connection
        else:
            pool = None
            pool_connection = None
            connection = self._connection

        if transaction_state is None:
            transaction_state = _transaction_state_for_connection(connection)
        generated_savepoint_name: str | None = None
        transaction = cast(_Transaction, connection.transaction())
        nested_before_start = _connection_top_transaction(connection) is not None
        nested_owned_by_relq = nested_before_start and bool(
            _controlled_transactions_for_connection(connection)
        )
        recovery_boundary = (
            transaction_state.stack[-1]
            if nested_owned_by_relq and transaction_state.stack
            else None
        )
        started = perf_counter()
        try:
            await transaction.start()
        except BaseException as error:
            start_sql = _transaction_control_sql(transaction, "start")
            try:
                self._observe(start_sql, (), started, None, error)
            finally:
                if nested_owned_by_relq:
                    await _cleanup_nested_transaction(
                        transaction,
                        connection,
                        recovery_boundary=recovery_boundary,
                    )
                elif pool is None:
                    try:
                        await _recover_asyncpg_connection(
                            connection,
                            owned_transaction=transaction,
                            recovery_control=self._run_recovery_control,
                        )
                    except BaseException as recovery_error:
                        raise recovery_error from error
                else:
                    await _release_pool_connection(pool, pool_connection)
            raise
        generated_savepoint_name = _generated_savepoint_name(transaction)
        try:
            self._observe(
                _transaction_control_sql(transaction, "start"),
                (),
                started,
                0,
                None,
            )
        except BaseException:
            if generated_savepoint_name is None:
                try:
                    await _recover_asyncpg_connection(
                        connection,
                        owned_transaction=transaction,
                        recovery_control=self._run_recovery_control,
                    )
                finally:
                    await _release_pool_connection(pool, pool_connection)
            else:
                await _cleanup_nested_transaction(
                    transaction,
                    connection,
                    recovery_boundary=recovery_boundary,
                )
            raise
        if generated_savepoint_name is not None:
            try:
                transaction_state.reserve_savepoint_name(generated_savepoint_name)
            except BaseException:
                await _cleanup_nested_transaction(
                    transaction,
                    connection,
                    recovery_boundary=recovery_boundary,
                )
                raise
        return ControlledTransaction(
            connection,
            transaction,
            observer=self._observer,
            pool=pool,
            pool_connection=pool_connection,
            transaction_state=transaction_state,
            generated_savepoint_name=generated_savepoint_name,
        )

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[PostgresDatabase]:
        controlled = await self.begin()
        try:
            yield controlled
        except BaseException:
            try:
                await controlled.rollback()
            except TransactionUnavailableError:
                pass
            raise
        else:
            await controlled.commit()

    @asynccontextmanager
    async def transaction_connection(self) -> AsyncGenerator[Connection]:
        """Yield the raw connection while an explicit transaction is open."""
        transaction = await self.begin()
        try:
            yield transaction.raw_connection
        except BaseException:
            try:
                await transaction.rollback()
            except TransactionUnavailableError:
                pass
            raise
        else:
            await transaction.commit()


class ControlledTransaction(PostgresDatabase):
    """A manually controlled asyncpg transaction and its query interface."""

    def __init__(
        self,
        connection: Connection,
        transaction: _Transaction,
        *,
        observer: QueryObserver | None,
        pool: asyncpg.Pool | None = None,
        pool_connection: asyncpg.pool.PoolConnectionProxy[asyncpg.Record] | None = None,
        transaction_state: _PostgresTransactionState,
        generated_savepoint_name: str | None = None,
    ) -> None:
        super().__init__(connection, observer=observer)
        self._transaction = transaction
        self._generated_savepoint_name = generated_savepoint_name
        self._pool = pool
        self._pool_connection = pool_connection
        self._raw_connection = connection
        self._transaction_state = transaction_state
        self._savepoint_names: set[str] = set()
        self._savepoints: list[_PostgresSavepoint] = []
        if generated_savepoint_name is not None:
            self._savepoint_names.add(generated_savepoint_name)
        self._transaction_state.register_transaction(self)
        self._closed = False
        self._connection_key = _connection_key(connection)
        _TRANSACTIONS_BY_CONNECTION.setdefault(self._connection_key, weakref.WeakSet()).add(self)

    async def begin(self) -> ControlledTransaction:
        self._ensure_usable()
        return await self._begin_controlled_transaction(self._transaction_state)

    def _ensure_usable(self) -> None:
        if self._closed:
            raise TransactionUnavailableError("controlled transaction is no longer usable")

    @property
    def raw_connection(self) -> Connection:
        return self._raw_connection

    def _close(self) -> None:
        self._clear_savepoint_names()
        self._closed = True
        self._transaction_state.remove_transaction(self)
        handles = _TRANSACTIONS_BY_CONNECTION.get(self._connection_key)
        if handles is not None:
            handles.discard(self)
            if not handles:
                _TRANSACTIONS_BY_CONNECTION.pop(self._connection_key, None)
        _discard_transaction_state(self._raw_connection, self._transaction_state)

    def _invalidate(self) -> None:
        self._close()

    def _invalidate_for_recovery(self) -> None:
        self._transaction_state.invalidate_all()

    def _invalidate_subtree_for_recovery(self) -> None:
        self._transaction_state.invalidate_after(self)
        self._invalidate()

    def _clear_savepoint_names(self) -> None:
        for savepoint in tuple(self._savepoints):
            savepoint._invalidate()  # pyright: ignore[reportPrivateUsage]
        self._savepoints.clear()
        for name in self._savepoint_names:
            self._transaction_state.release_savepoint_name(name)
        self._savepoint_names.clear()

    def _forget_savepoint(self, savepoint: _PostgresSavepoint) -> None:
        if savepoint in self._savepoints:
            self._savepoints.remove(savepoint)
        self._transaction_state.remove_entry(savepoint)
        name = savepoint.name
        self._savepoint_names.discard(name)
        self._transaction_state.release_savepoint_name(name)

    def _invalidate_descendant_savepoints(self, savepoint: _PostgresSavepoint) -> None:
        self._transaction_state.invalidate_after(savepoint)

    def _release_savepoint(self, savepoint: _PostgresSavepoint) -> None:
        self._transaction_state.invalidate_after(savepoint)
        self._forget_savepoint(savepoint)

    async def _release_connection(self) -> None:
        pool = self._pool
        pool_connection = self._pool_connection
        self._pool_connection = None
        if pool is not None and pool_connection is not None:
            await pool.release(pool_connection)

    async def commit(self) -> None:
        self._ensure_usable()
        self._transaction_state.invalidate_after(self)
        sql = _transaction_control_sql(
            self._transaction,
            "commit",
        )
        try:
            started = await self._run_control(sql, self._transaction.commit)
        except BaseException as commit_error:  # noqa: BLE001
            await self._recover_after_control_failure(commit_error)
        else:
            self._close()
            self._observe(sql, (), started, 0, None)
        finally:
            await self._release_connection()

    async def rollback(self) -> None:
        self._ensure_usable()
        self._transaction_state.invalidate_after(self)
        nested = self._generated_savepoint_name is not None
        sql = _transaction_control_sql(
            self._transaction,
            "rollback",
        )
        observer_error: BaseException | None = None
        try:
            started = await self._run_control(sql, self._transaction.rollback)
            if nested:
                try:
                    self._observe(sql, (), started, 0, None)
                except BaseException as error:  # noqa: BLE001
                    observer_error = error
                release_sql = f"release savepoint {self._generated_savepoint_name}"
                release_started = await self._run_control(
                    release_sql,
                    lambda: self.raw_connection.execute(release_sql),
                )
                try:
                    self._observe(release_sql, (), release_started, 0, None)
                except BaseException as error:  # noqa: BLE001
                    if observer_error is None:
                        observer_error = error
        except BaseException as rollback_error:  # noqa: BLE001
            await self._recover_after_control_failure(rollback_error)
        else:
            self._close()
            if not nested:
                self._observe(sql, (), started, 0, None)
            if observer_error is not None:
                raise observer_error
        finally:
            await self._release_connection()

    async def savepoint(self, name: str) -> Savepoint:
        self._ensure_usable()
        quoted = _quote_savepoint(name)
        self._transaction_state.reserve_savepoint_name(quoted)
        sql = f"savepoint {quoted}"
        try:
            started = await self._run_control(sql, lambda: self.raw_connection.execute(sql))
        except BaseException:
            self._transaction_state.release_savepoint_name(quoted)
            raise
        self._savepoint_names.add(quoted)
        savepoint = _PostgresSavepoint(self, quoted)
        self._savepoints.append(savepoint)
        self._transaction_state.register_savepoint(savepoint)
        try:
            self._observe(sql, (), started, 0, None)
        except BaseException:
            savepoint._invalidate()  # pyright: ignore[reportPrivateUsage]
            try:
                await self.raw_connection.execute(f"release savepoint {quoted}")
            except BaseException:
                _LOGGER.debug("failed to clean up an observer-interrupted savepoint", exc_info=True)
            raise
        return savepoint

    async def _recover_connection(self) -> None:
        await _recover_asyncpg_connection(
            self.raw_connection,
            extra=(self,),
            recovery_boundary=self,
            recovery_control=self._run_recovery_control,
        )

    async def _recover_after_control_failure(self, operation_error: BaseException) -> NoReturn:
        try:
            # After a control-operation error asyncpg marks the transaction FAILED
            # and refuses to roll it back through Transaction.rollback(). A raw
            # ROLLBACK resets the connection and clears asyncpg's top-transaction
            # pointer. Recovery also invalidates the registry.
            await self._recover_connection()
        except BaseException as recovery_error:
            raise recovery_error from operation_error
        raise operation_error


class _PostgresSavepoint:
    def __init__(self, transaction: ControlledTransaction, quoted_name: str) -> None:
        self._transaction = transaction
        self._name = quoted_name
        self._released = False
        self._invalidated = False

    def _invalidate(self) -> None:
        if self._released or self._invalidated:
            return
        self._transaction._forget_savepoint(self)  # pyright: ignore[reportPrivateUsage]
        self._invalidated = True

    @property
    def name(self) -> str:
        return self._name

    def _ensure_usable(self) -> None:
        self._transaction._ensure_usable()  # pyright: ignore[reportPrivateUsage]
        if self._released or self._invalidated:
            raise TransactionUnavailableError("savepoint is no longer usable")

    async def release(self) -> None:
        self._ensure_usable()
        sql = f"release savepoint {self._name}"
        try:
            started = await self._transaction._run_control(  # pyright: ignore[reportPrivateUsage]
                sql, lambda: self._transaction.raw_connection.execute(sql)
            )
        except BaseException as error:  # noqa: BLE001
            self._transaction._invalidate_descendant_savepoints(self)  # pyright: ignore[reportPrivateUsage]
            self._invalidate()
            await self._transaction._recover_after_control_failure(  # pyright: ignore[reportPrivateUsage]
                error
            )
        else:
            self._transaction._release_savepoint(self)  # pyright: ignore[reportPrivateUsage]
            self._released = True
            self._transaction._observe(sql, (), started, 0, None)  # pyright: ignore[reportPrivateUsage]

    async def rollback(self) -> None:
        self._ensure_usable()
        sql = f"rollback to savepoint {self._name}"
        try:
            started = await self._transaction._run_control(  # pyright: ignore[reportPrivateUsage]
                sql, lambda: self._transaction.raw_connection.execute(sql)
            )
        except BaseException as error:  # noqa: BLE001
            self._transaction._invalidate_descendant_savepoints(self)  # pyright: ignore[reportPrivateUsage]
            self._invalidate()
            await self._transaction._recover_after_control_failure(  # pyright: ignore[reportPrivateUsage]
                error
            )
        else:
            self._transaction._invalidate_descendant_savepoints(self)  # pyright: ignore[reportPrivateUsage]
            self._transaction._observe(sql, (), started, 0, None)  # pyright: ignore[reportPrivateUsage]


def _quote_savepoint(name: str) -> str:
    if not name:
        raise ValueError("savepoint name must not be empty")
    if len(name.encode("utf-8")) > 63:
        raise ValueError("PostgreSQL savepoint names must be at most 63 UTF-8 bytes")
    return '"' + name.replace('"', '""') + '"'


def _savepoint_name_key(name: str) -> str:
    return "".join(
        chr(ord(character) + (ord("a") - ord("A"))) if "A" <= character <= "Z" else character
        for character in name
    )


def _transaction_control_sql(
    transaction: _Transaction, operation: Literal["start", "commit", "rollback"]
) -> str:
    name = _generated_savepoint_name(transaction)
    if name is None:
        if operation == "start":
            return "begin"
        if operation == "commit":
            return "commit"
        if operation == "rollback":
            return "rollback"
    else:
        if operation == "start":
            return f"savepoint {name}"
        if operation == "commit":
            return f"release savepoint {name}"
        if operation == "rollback":
            return f"rollback to {name}"
    raise ValueError(f"unsupported transaction operation: {operation!r}")


def _generated_savepoint_name(transaction: _Transaction) -> str | None:
    name = cast(_AsyncpgTransactionInternals, transaction)._id  # pyright: ignore[reportPrivateUsage]
    if not isinstance(name, str):
        return None
    return _quote_savepoint(name)


def _underlying_connection(connection: Connection) -> _AsyncpgConnectionInternals | None:
    if isinstance(connection, asyncpg.pool.PoolConnectionProxy):
        try:
            return cast(
                _AsyncpgConnectionInternals | None,
                object.__getattribute__(connection, "_con"),
            )
        except AttributeError:
            return None
    return cast(_AsyncpgConnectionInternals, connection)


def _connection_key(connection: Connection) -> int:
    underlying = _underlying_connection(connection)
    return id(connection) if underlying is None else id(underlying)


def _connection_top_transaction(connection: Connection) -> object | None:
    underlying = _underlying_connection(connection)
    return None if underlying is None else underlying._top_xact  # pyright: ignore[reportPrivateUsage]


def _controlled_transactions_for_connection(connection: Connection) -> set[ControlledTransaction]:
    return set(_TRANSACTIONS_BY_CONNECTION.get(_connection_key(connection), ()))


def _transaction_state_for_connection(connection: Connection) -> _PostgresTransactionState:
    key = _connection_key(connection)
    state = _TRANSACTION_STATES_BY_CONNECTION.get(key)
    if state is None:
        state = _PostgresTransactionState()
        _TRANSACTION_STATES_BY_CONNECTION[key] = state
    return state


def _discard_transaction_state(connection: Connection, state: _PostgresTransactionState) -> None:
    key = _connection_key(connection)
    if not state.entries and _TRANSACTION_STATES_BY_CONNECTION.get(key) is state:
        _TRANSACTION_STATES_BY_CONNECTION.pop(key, None)


async def _release_pool_connection(
    pool: asyncpg.Pool | None,
    pool_connection: asyncpg.pool.PoolConnectionProxy[asyncpg.Record] | None,
) -> None:
    if pool is not None:
        assert pool_connection is not None
        await pool.release(pool_connection)


async def _cleanup_nested_transaction(
    transaction: _Transaction,
    connection: Connection,
    *,
    recovery_boundary: ControlledTransaction | None,
) -> None:
    try:
        await transaction.rollback()
        name = _generated_savepoint_name(transaction)
        if name is not None:
            await connection.execute(f"release savepoint {name}")
    except BaseException as cleanup_error:
        try:
            await _recover_asyncpg_connection(
                connection,
                owned_transaction=transaction,
                recovery_boundary=recovery_boundary,
                recovery_control=(
                    None if recovery_boundary is None else recovery_boundary._run_recovery_control  # pyright: ignore[reportPrivateUsage]
                ),
            )
        except BaseException as recovery_error:
            raise recovery_error from cleanup_error
        _LOGGER.debug("failed to clean up an observer-interrupted savepoint", exc_info=True)


async def _recover_asyncpg_connection(
    connection: Connection,
    *,
    extra: tuple[ControlledTransaction, ...] = (),
    owned_transaction: _Transaction | None = None,
    recovery_boundary: ControlledTransaction | None = None,
    recovery_control: Callable[[Connection, str], Awaitable[None]] | None = None,
) -> None:
    handles = _controlled_transactions_for_connection(connection)
    handles.update(extra)
    underlying = _underlying_connection(connection)
    current_top_transaction = (
        None if underlying is None else underlying._top_xact  # pyright: ignore[reportPrivateUsage]
    )
    if not handles and current_top_transaction is not owned_transaction:
        # No relq handle exists and the attempted transaction never became
        # asyncpg's top transaction. The connection may hold caller-owned work,
        # so recovery must not issue a raw ROLLBACK.
        return

    if (
        recovery_boundary is not None and recovery_boundary._generated_savepoint_name is not None  # pyright: ignore[reportPrivateUsage]
    ):
        recovery_boundary._invalidate_subtree_for_recovery()  # pyright: ignore[reportPrivateUsage]
        name = recovery_boundary._generated_savepoint_name  # pyright: ignore[reportPrivateUsage]
        rollback_sql = f"rollback to {name}"
        release_sql = f"release savepoint {name}"
        if recovery_control is None:
            await connection.execute(rollback_sql)
            await connection.execute(release_sql)
        else:
            await recovery_control(connection, rollback_sql)
            await recovery_control(connection, release_sql)
        return

    for handle in tuple(handles):
        handle._invalidate_for_recovery()  # pyright: ignore[reportPrivateUsage]

    recovery_error: BaseException | None = None
    if underlying is None:
        recovery_error = RuntimeError("asyncpg pool connection proxy is detached")
    if recovery_error is None:
        try:
            object.__setattr__(underlying, "_top_xact", None)
            if recovery_control is None:
                await connection.execute("rollback")
            else:
                await recovery_control(connection, "rollback")
        except BaseException as error:  # noqa: BLE001
            recovery_error = error

    release_error: BaseException | None = None
    for handle in handles:
        try:
            await handle._release_connection()  # pyright: ignore[reportPrivateUsage]
        except BaseException as error:  # noqa: BLE001
            if release_error is None:
                release_error = error

    if recovery_error is not None:
        if release_error is not None:
            raise recovery_error from release_error
        raise recovery_error
    if release_error is not None:
        raise release_error


def _encode_interval(value: object) -> tuple[int, int, int]:
    if not isinstance(value, Interval):
        raise TypeError("PostgreSQL interval parameters must be relq.Interval values")
    return (value.months, value.days, value.microseconds)


def _decode_interval(value: tuple[int, int, int]) -> Interval:
    months, days, microseconds = value
    return Interval(months, days, microseconds)


def _command_count(status: str) -> int:
    """Extract the affected-row count from asyncpg's PostgreSQL command tag."""
    _, _, count = status.rpartition(" ")
    if not count.isdecimal():
        raise RuntimeError(f"asyncpg returned an invalid command status: {status!r}")
    return int(count)


async def _stream_rows[Row](
    query: Query[Row],
    connection: Connection,
    sql: str,
    parameters: tuple[object, ...],
    on_row: Callable[[], None],
    on_error: Callable[[BaseException], None],
) -> AsyncGenerator[Row]:
    try:
        async for record in connection.cursor(sql, *parameters):
            row = map_row(query, tuple(record))
            on_row()
            yield row
    except BaseException as error:
        on_error(error)
        raise
