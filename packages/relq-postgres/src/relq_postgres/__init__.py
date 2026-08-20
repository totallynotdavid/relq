"""Asynchronous asyncpg executor for relq."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from relq import Interval, RowAdapter
from relq._compiler.api import compile_postgres
from relq._execution import (
    Command,
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

    def __init__(
        self,
        connection: asyncpg.Connection
        | asyncpg.Pool
        | asyncpg.pool.PoolConnectionProxy[asyncpg.Record],
    ) -> None:
        self._connection: (
            asyncpg.Connection | asyncpg.Pool | asyncpg.pool.PoolConnectionProxy[asyncpg.Record]
        ) = connection

    @staticmethod
    async def _configure_connection(
        connection: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy[asyncpg.Record],
    ) -> None:
        await connection.set_type_codec(
            "interval",
            schema="pg_catalog",
            encoder=_encode_interval,
            decoder=_decode_interval,
            format="tuple",
        )

    @asynccontextmanager
    async def _connection_scope(
        self,
    ) -> AsyncGenerator[asyncpg.Connection | asyncpg.pool.PoolConnectionProxy[asyncpg.Record]]:
        """Acquire and configure exactly one physical connection per operation."""
        if isinstance(self._connection, asyncpg.Pool):
            async with self._connection.acquire() as connection:
                await self._configure_connection(connection)
                yield connection
            return
        await self._configure_connection(self._connection)
        yield self._connection

    async def fetch_all[Row](self, query: Query[Row]) -> list[Row]:
        compiled = compile_postgres(query)
        async with self._connection_scope() as connection:
            records = await connection.fetch(compiled.sql, *compiled.parameters)
        return map_all(query, (tuple(record) for record in records))

    async def fetch_one[Row](self, query: Query[Row]) -> Row | None:
        compiled = compile_postgres(query)
        async with self._connection_scope() as connection:
            row = await connection.fetchrow(compiled.sql, *compiled.parameters)
        return map_one(query, None if row is None else tuple(row))

    async def fetch_all_as[Row, Model](
        self, query: Query[Row], adapter: RowAdapter[Model]
    ) -> list[Model]:
        """Map result rows through an explicit, arity-validating adapter."""
        if extract_query(query).adapter is not None:
            raise TypeError("query already declares a result model; use fetch_all()")
        compiled = compile_postgres(query)
        async with self._connection_scope() as connection:
            rows = await connection.fetch(compiled.sql, *compiled.parameters)
        return [adapter.map(tuple(row)) for row in rows]

    async def fetch_one_as[Row, Model](
        self, query: Query[Row], adapter: RowAdapter[Model]
    ) -> Model | None:
        if extract_query(query).adapter is not None:
            raise TypeError("query already declares a result model; use fetch_one()")
        compiled = compile_postgres(query)
        async with self._connection_scope() as connection:
            row = await connection.fetchrow(compiled.sql, *compiled.parameters)
        return None if row is None else adapter.map(tuple(row))

    @asynccontextmanager
    async def fetch_iter[Row](self, query: Query[Row]) -> AsyncGenerator[AsyncIterator[Row]]:
        """Stream rows through a server-side cursor instead of materializing them.

        PostgreSQL cursors are only valid inside a transaction, so this opens
        one for the scope of iteration (nesting as a savepoint inside an outer
        relq transaction) and, on a pool, holds one acquired connection for
        that same scope.
        """
        compiled = compile_postgres(query)
        async with self._connection_scope() as connection, connection.transaction():
            yield _stream_rows(query, connection, compiled.sql, compiled.parameters)

    async def execute[Row](self, query: Command[Row]) -> int:
        require_command(query)
        compiled = compile_postgres(query)
        async with self._connection_scope() as connection:
            # execute() with bound parameters shares asyncpg's prepared-statement
            # cache with fetch()/fetchrow() (both route through Connection._execute).
            # Only zero-parameter calls (e.g. DEFAULT VALUES) skip it, via asyncpg's
            # simple-query protocol.
            status = await connection.execute(compiled.sql, *compiled.parameters)
        return _command_count(status)

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[PostgresDatabase]:
        async with self._connection_scope() as connection, connection.transaction():
            yield (
                self
                if not isinstance(self._connection, asyncpg.Pool)
                else PostgresDatabase(connection)
            )


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
