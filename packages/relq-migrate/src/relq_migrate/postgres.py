"""Asynchronous PostgreSQL migration adapter."""

from __future__ import annotations

import zlib
from typing import cast

import asyncpg

from ._model import (
    Migration,
    MigrationError,
    MigrationReport,
    MigrationResult,
    MigrationStatus,
    migration_checksum,
    pending_migrations,
    quote_identifier,
    validate_identifier,
)
from .provider import FileMigrationProvider

type _Connection = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy[asyncpg.Record]
_LOCK_NAMESPACE = 0x52454C51


class Migrator:
    """Apply filesystem migrations through asyncpg.

    The advisory lock is session-scoped and held across each migration's own
    transaction. This preserves successful earlier migrations if a later file
    fails while ensuring another migrator cannot observe or apply the same
    pending file concurrently.
    """

    def __init__(
        self,
        connection: asyncpg.Connection | asyncpg.Pool,
        provider: FileMigrationProvider,
        *,
        table_name: str = "relq_migrations",
    ) -> None:
        self._connection = connection
        self._provider = provider
        self._table_name = validate_identifier(table_name, kind="history table")

    async def migrate_to_latest(self) -> MigrationReport:
        migrations = self._provider.migrations()
        if isinstance(self._connection, asyncpg.Pool):
            async with self._connection.acquire() as connection:
                return await self._migrate_connection(connection, migrations)
        return await self._migrate_connection(self._connection, migrations)

    async def _migrate_connection(
        self, connection: _Connection, migrations: tuple[Migration, ...]
    ) -> MigrationReport:
        if connection.is_in_transaction():
            raise RuntimeError(
                "PostgresMigrator requires a connection outside a caller-owned transaction"
            )
        table = quote_identifier(self._table_name)
        lock_key = _advisory_lock_key(self._table_name)
        locked = False
        try:
            await connection.execute("SELECT pg_advisory_lock($1)", lock_key)
            locked = True
            await connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    migration_name text PRIMARY KEY,
                    checksum text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            rows = await connection.fetch(f"SELECT migration_name, checksum FROM {table}")
            applied = {cast(str, row["migration_name"]): cast(str, row["checksum"]) for row in rows}
            pending = pending_migrations(migrations, applied)
            results: list[MigrationResult] = []
            for index, migration in enumerate(pending):
                try:
                    async with connection.transaction():
                        transaction_id = cast(
                            int, await connection.fetchval("SELECT txid_current()")
                        )
                        await connection.execute(migration.sql)
                        if not connection.is_in_transaction():
                            raise MigrationError(
                                f"migration {migration.name!r} ended the migrator's transaction"
                            )
                        current_transaction_id = cast(
                            int, await connection.fetchval("SELECT txid_current()")
                        )
                        if current_transaction_id != transaction_id:
                            raise MigrationError(
                                f"migration {migration.name!r} replaced the migrator's transaction"
                            )
                        await connection.execute(
                            f"INSERT INTO {table} (migration_name, checksum) VALUES ($1, $2)",
                            migration.name,
                            migration_checksum(migration),
                        )
                except Exception as error:  # noqa: BLE001 - report migration failures
                    results.append(MigrationResult(migration.name, MigrationStatus.ERROR, error))
                    results.extend(
                        MigrationResult(
                            later.name,
                            MigrationStatus.NOT_EXECUTED,
                        )
                        for later in pending[index + 1 :]
                    )
                    return MigrationReport(error, tuple(results))
                results.append(MigrationResult(migration.name, MigrationStatus.SUCCESS))
            return MigrationReport(None, tuple(results))
        except Exception as error:  # noqa: BLE001 - report migration failures
            return MigrationReport(error, ())
        finally:
            if locked:
                await connection.execute("SELECT pg_advisory_unlock($1)", lock_key)


PostgresMigrator = Migrator


def _advisory_lock_key(table_name: str) -> int:
    """Return a stable 64-bit key for the history table."""
    return (_LOCK_NAMESPACE << 32) | zlib.crc32(table_name.encode("utf-8"))
