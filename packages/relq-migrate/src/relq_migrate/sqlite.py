"""Synchronous SQLite migration adapter."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import cast

from ._model import (
    MigrationError,
    MigrationReport,
    MigrationResult,
    MigrationStatus,
    is_sql_empty,
    migration_checksum,
    pending_migrations,
    quote_identifier,
    validate_identifier,
)
from .provider import FileMigrationProvider


class SQLiteMigrator:
    """Apply filesystem migrations through a synchronous SQLite connection.

    SQLite has no advisory lock. ``BEGIN IMMEDIATE`` obtains the database write
    lock before reading the history table, so concurrent migrators serialize.
    Each migration and its history record are one transaction. A failed
    migration therefore leaves earlier successful migrations committed, as the
    PostgreSQL adapter does. The adapter supports legacy transaction control
    and ``autocommit=True``. It does not support ``autocommit=False``.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        provider: FileMigrationProvider,
        *,
        table_name: str = "relq_migrations",
    ) -> None:
        _validate_connection_mode(connection)
        self._connection = connection
        self._provider = provider
        self._table_name = validate_identifier(table_name, kind="history table")

    def migrate_to_latest(self) -> MigrationReport:
        _validate_connection_mode(self._connection)
        table = quote_identifier(self._table_name)
        results: list[MigrationResult] = []

        if self._connection.in_transaction:
            raise RuntimeError(
                "SQLiteMigrator requires a connection outside a caller-owned transaction"
            )

        try:
            migrations = self._provider.migrations()
            self._connection.execute("BEGIN IMMEDIATE")
            _create_history_table(self._connection, table)
            applied = _read_applied(self._connection, table)
            pending = pending_migrations(migrations, applied)
            _commit(self._connection)
        except Exception as error:  # noqa: BLE001 - report migration failures
            _rollback(self._connection)
            return MigrationReport(error, ())

        for index, migration in enumerate(pending):
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                applied = _read_applied(self._connection, table)
                pending_now = pending_migrations(migrations, applied)
                if migration.name in applied:
                    _commit(self._connection)
                    results.append(MigrationResult(migration.name, MigrationStatus.NOT_EXECUTED))
                    continue
                if not pending_now or pending_now[0].name != migration.name:
                    raise MigrationError(
                        f"migration {migration.name!r} is not the next pending migration"
                    )
                for statement in _split_sql_statements(migration.sql):
                    if _is_transaction_control(self._connection, statement):
                        raise MigrationError(
                            f"migration {migration.name!r} contains transaction-control SQL"
                        )
                    self._connection.execute(statement)
                    if not self._connection.in_transaction:
                        raise MigrationError(
                            f"migration {migration.name!r} ended the migrator's transaction"
                        )
                self._connection.execute(
                    f"INSERT INTO {table} (migration_name, checksum) VALUES (?, ?)",
                    (migration.name, migration_checksum(migration)),
                )
                _commit(self._connection)
            except Exception as error:  # noqa: BLE001 - report migration failures
                results.append(MigrationResult(migration.name, MigrationStatus.ERROR, error))
                results.extend(
                    MigrationResult(later.name, MigrationStatus.NOT_EXECUTED)
                    for later in pending[index + 1 :]
                )
                _rollback(self._connection)
                return MigrationReport(error, tuple(results))
            results.append(MigrationResult(migration.name, MigrationStatus.SUCCESS))
        return MigrationReport(None, tuple(results))


def _validate_connection_mode(connection: sqlite3.Connection) -> None:
    if connection.autocommit is False:
        raise ValueError(
            "SQLiteMigrator does not support sqlite3 autocommit=False; use "
            "autocommit=True or sqlite3.LEGACY_TRANSACTION_CONTROL"
        )


def _commit(connection: sqlite3.Connection) -> None:
    if connection.autocommit is True:
        connection.execute("COMMIT")
    else:
        connection.commit()


def _rollback(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        return
    if connection.autocommit is True:
        connection.execute("ROLLBACK")
    else:
        connection.rollback()


def _create_history_table(connection: sqlite3.Connection, table: str) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            migration_name TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _read_applied(connection: sqlite3.Connection, table: str) -> dict[str, str]:
    cursor = connection.cursor()
    cursor.row_factory = None
    try:
        raw_rows = cursor.execute(f"SELECT migration_name, checksum FROM {table}").fetchall()
    finally:
        cursor.close()
    applied: dict[str, str] = {}
    for raw_row in cast(list[object], raw_rows):
        if not isinstance(raw_row, tuple):
            raise MigrationError("migration history query did not return two columns")
        row = cast(tuple[object, ...], raw_row)
        applied[_history_text(row[0], "migration_name")] = _history_text(row[1], "checksum")
    return applied


def _history_text(value: object, column: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MigrationError(f"migration history {column} is not valid UTF-8") from error
    raise MigrationError(f"migration history {column} is not text")


def _is_transaction_control(connection: sqlite3.Connection, statement: str) -> bool:
    """Detect transaction-control SQL with SQLite's own compiler, before execution.

    ``EXPLAIN`` compiles a statement without applying it, and SQLite compiles
    transaction controls to the ``AutoCommit`` and ``Savepoint`` opcodes. An
    authorizer would also work, but ``sqlite3.Connection`` has no authorizer
    getter, so the caller's callback could not be restored afterwards. The
    ``in_transaction`` check after each execute remains as a second defense.
    """
    if is_sql_empty(statement):
        return False
    cursor = connection.cursor()
    cursor.row_factory = None
    try:
        rows = cursor.execute(f"EXPLAIN {statement}").fetchall()
    finally:
        cursor.close()
    return any(
        isinstance(row, tuple)
        and len(cast(tuple[object, ...], row)) > 1
        and cast(tuple[object, ...], row)[1]
        in {"AutoCommit", b"AutoCommit", "Savepoint", b"Savepoint"}
        for row in cast(list[object], rows)
    )


def _split_sql_statements(sql: str) -> Iterator[str]:
    """Split a SQLite script at complete semicolons."""
    buffer: list[str] = []
    for character in sql:
        buffer.append(character)
        if character == ";" and sqlite3.complete_statement("".join(buffer)):
            statement = "".join(buffer).strip()
            if statement:
                yield statement
            buffer.clear()

    statement = "".join(buffer).strip()
    if statement:
        yield statement
