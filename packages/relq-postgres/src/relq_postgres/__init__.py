"""Asynchronous asyncpg executor for relq."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import overload

import asyncpg
from relq import Interval, RowAdapter
from relq._compiler.api import compile_postgres
from relq._execution import (
    Command,
    MappedResultQuery,
    RawResultQuery,
    map_all,
    map_one,
    map_row,
    require_command,
)
from relq._query import Query, extract_query

__all__ = ["PostgresDatabase"]


class PostgresDatabase:
    """Execute relq queries through an existing asyncpg pool or connection.

    The caller owns pool and connection lifecycle. Transactions on a pool
    acquire one connection for their complete scope; transactions on a direct
    connection reuse that connection. If that pool or connection sits behind
    pgbouncer in ``transaction`` or ``statement`` pooling mode, pass
    ``statement_cache_size=0`` to ``asyncpg.connect``/``create_pool``: asyncpg's
    automatic prepared-statement cache does not survive that pooling mode.
    """

    def __init__(self, connection: asyncpg.Connection | asyncpg.Pool) -> None:
        self._connection: (
            asyncpg.Connection | asyncpg.Pool | asyncpg.pool.PoolConnectionProxy[asyncpg.Record]
        ) = connection

    @classmethod
    def _from_pool_connection(
        cls, connection: asyncpg.pool.PoolConnectionProxy[asyncpg.Record]
    ) -> PostgresDatabase:
        database = object.__new__(cls)
        database._connection = connection
        return database

    async def _configure_connection(self) -> None:
        if isinstance(self._connection, asyncpg.Pool):
            return
        await self._connection.set_type_codec(
            "interval",
            schema="pg_catalog",
            encoder=_encode_interval,
            decoder=_decode_interval,
            format="tuple",
        )

    @overload
    async def fetch_all[Row](self, query: RawResultQuery[Row]) -> list[Row]: ...

    @overload
    async def fetch_all[Model](self, query: MappedResultQuery[Model]) -> list[Model]: ...

    async def fetch_all(
        self,
        query: (RawResultQuery[object] | MappedResultQuery[object]),
    ) -> object:
        compiled = compile_postgres(query)
        if isinstance(self._connection, asyncpg.Pool):
            async with self._connection.acquire() as connection:
                database = PostgresDatabase._from_pool_connection(connection)
                await database._configure_connection()
                records = await connection.fetch(compiled.sql, *compiled.parameters)
        else:
            await self._configure_connection()
            records = await self._connection.fetch(compiled.sql, *compiled.parameters)
        return map_all(query, (tuple(record) for record in records))

    @overload
    async def fetch_one[Row](self, query: RawResultQuery[Row]) -> Row | None: ...

    @overload
    async def fetch_one[Model](self, query: MappedResultQuery[Model]) -> Model | None: ...

    async def fetch_one(
        self,
        query: (RawResultQuery[object] | MappedResultQuery[object]),
    ) -> object:
        compiled = compile_postgres(query)
        await self._configure_connection()
        row = await self._connection.fetchrow(compiled.sql, *compiled.parameters)
        return map_one(query, None if row is None else tuple(row))

    async def fetch_all_as[Row, Model](
        self, query: RawResultQuery[Row], adapter: RowAdapter[Model]
    ) -> list[Model]:
        """Map result rows through an explicit, arity-validating adapter."""
        if extract_query(query).adapter is not None:
            raise TypeError("query already declares a result model; use fetch_all()")
        compiled = compile_postgres(query)
        await self._configure_connection()
        rows = await self._connection.fetch(compiled.sql, *compiled.parameters)
        return [adapter.map(tuple(row)) for row in rows]

    async def fetch_one_as[Row, Model](
        self, query: RawResultQuery[Row], adapter: RowAdapter[Model]
    ) -> Model | None:
        if extract_query(query).adapter is not None:
            raise TypeError("query already declares a result model; use fetch_one()")
        compiled = compile_postgres(query)
        await self._configure_connection()
        row = await self._connection.fetchrow(compiled.sql, *compiled.parameters)
        return None if row is None else adapter.map(tuple(row))

    @overload
    def fetch_iter[Row](
        self, query: RawResultQuery[Row]
    ) -> AbstractAsyncContextManager[AsyncIterator[Row]]: ...

    @overload
    def fetch_iter[Model](
        self, query: MappedResultQuery[Model]
    ) -> AbstractAsyncContextManager[AsyncIterator[Model]]: ...

    @asynccontextmanager
    async def fetch_iter(
        self,
        query: (RawResultQuery[object] | MappedResultQuery[object]),
    ) -> AsyncGenerator[AsyncIterator[object]]:
        """Stream rows through a server-side cursor instead of materializing them.

        PostgreSQL cursors are only valid inside a transaction, so this opens
        one for the scope of iteration (nesting as a savepoint inside an outer
        relq transaction) and, on a pool, holds one acquired connection for
        that same scope.
        """
        compiled = compile_postgres(query)
        if isinstance(self._connection, asyncpg.Pool):
            async with self._connection.acquire() as connection:
                database = PostgresDatabase._from_pool_connection(connection)
                await database._configure_connection()
                async with connection.transaction():
                    yield _stream_rows(query, connection, compiled.sql, compiled.parameters)
        else:
            await self._configure_connection()
            async with self._connection.transaction():
                yield _stream_rows(query, self._connection, compiled.sql, compiled.parameters)

    async def execute[Row](self, query: Command[Row]) -> int:
        require_command(query)
        compiled = compile_postgres(query)
        await self._configure_connection()
        # execute() with bound parameters shares asyncpg's prepared-statement
        # cache with fetch()/fetchrow() (both route through Connection._execute).
        # Only zero-parameter calls (e.g. DEFAULT VALUES) skip it, via asyncpg's
        # simple-query protocol.
        status = await self._connection.execute(compiled.sql, *compiled.parameters)
        return _command_count(status)

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[PostgresDatabase]:
        if isinstance(self._connection, asyncpg.Pool):
            async with self._connection.acquire() as connection:
                database = PostgresDatabase._from_pool_connection(connection)
                await database._configure_connection()
                async with connection.transaction():
                    yield database
        else:
            await self._configure_connection()
            async with self._connection.transaction():
                yield self


def _command_count(status: str) -> int:
    """Extract the affected-row count from asyncpg's PostgreSQL command tag."""
    _, _, count = status.rpartition(" ")
    if not count.isdecimal():
        raise RuntimeError(f"asyncpg returned an invalid command status: {status!r}")
    return int(count)


def _encode_interval(value: object) -> tuple[int, int, int]:
    if not isinstance(value, Interval):
        raise TypeError("PostgreSQL interval parameters must be relq.Interval values")
    return (value.months, value.days, value.microseconds)


def _decode_interval(value: tuple[int, int, int]) -> Interval:
    months, days, microseconds = value
    return Interval(months, days, microseconds)


async def _stream_rows[Row](
    query: Query[Row],
    connection: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy[asyncpg.Record],
    sql: str,
    parameters: tuple[object, ...],
) -> AsyncGenerator[Row]:
    async for record in connection.cursor(sql, *parameters):
        yield map_row(query, tuple(record))
