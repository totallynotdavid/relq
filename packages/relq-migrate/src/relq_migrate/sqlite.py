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
    migration_checksum,
    pending_migrations,
    quote_identifier,
    validate_identifier,
)
from .provider import FileMigrationProvider


class Migrator:
    """Apply filesystem migrations through a synchronous SQLite connection.

    SQLite has no advisory lock. ``BEGIN IMMEDIATE`` obtains the database write
    lock before reading the history table, so concurrent migrators serialize.
    Each migration and its history record are one transaction. A failed
    migration therefore leaves earlier successful migrations committed, just as
    the PostgreSQL adapter does.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        provider: FileMigrationProvider,
        *,
        table_name: str = "relq_migrations",
    ) -> None:
        self._connection = connection
        self._provider = provider
        self._table_name = validate_identifier(table_name, kind="history table")

    def migrate_to_latest(self) -> MigrationReport:
        migrations = self._provider.migrations()
        table = quote_identifier(self._table_name)
        results: list[MigrationResult] = []

        if self._connection.in_transaction:
            return MigrationReport(
                MigrationError("SQLite connection already owns a transaction"),
                (),
            )

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            _create_history_table(self._connection, table)
            applied = _read_applied(self._connection, table)
            pending = pending_migrations(migrations, applied)
            self._connection.commit()
        except Exception as error:  # noqa: BLE001 - report migration failures
            self._connection.rollback()
            return MigrationReport(error, ())

        for index, migration in enumerate(pending):
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                applied = _read_applied(self._connection, table)
                pending_now = pending_migrations(migrations, applied)
                if migration.name in applied:
                    self._connection.commit()
                    results.append(MigrationResult(migration.name, MigrationStatus.NOT_EXECUTED))
                    continue
                if not pending_now or pending_now[0].name != migration.name:
                    raise MigrationError(
                        f"migration {migration.name!r} is not the next pending migration"
                    )
                for statement in _split_sql_statements(migration.sql):
                    self._connection.execute(statement)
                    if not self._connection.in_transaction:
                        raise MigrationError(
                            f"migration {migration.name!r} ended the migrator's transaction"
                        )
                self._connection.execute(
                    f"INSERT INTO {table} (migration_name, checksum) VALUES (?, ?)",
                    (migration.name, migration_checksum(migration)),
                )
                self._connection.commit()
            except Exception as error:  # noqa: BLE001 - report migration failures
                results.append(MigrationResult(migration.name, MigrationStatus.ERROR, error))
                results.extend(
                    MigrationResult(later.name, MigrationStatus.NOT_EXECUTED)
                    for later in pending[index + 1 :]
                )
                self._connection.rollback()
                return MigrationReport(error, tuple(results))
            results.append(MigrationResult(migration.name, MigrationStatus.SUCCESS))
        return MigrationReport(None, tuple(results))


SQLiteMigrator = Migrator


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
    rows = cast(
        list[tuple[object, ...]],
        connection.execute(f"SELECT migration_name, checksum FROM {table}").fetchall(),
    )
    return {str(row[0]): str(row[1]) for row in rows}


def _split_sql_statements(sql: str) -> Iterator[str]:
    """Split a SQLite script at complete semicolons, including same-line ones."""
    buffer: list[str] = []
    state: str | None = None
    index = 0
    while index < len(sql):
        character = sql[index]
        buffer.append(character)

        if state == "line-comment":
            if character in "\r\n":
                state = None
        elif state == "block-comment":
            if character == "*" and index + 1 < len(sql) and sql[index + 1] == "/":
                buffer.append("/")
                index += 1
                state = None
        elif state in {"'", '"', "`"}:
            if character == state:
                if index + 1 < len(sql) and sql[index + 1] == state:
                    buffer.append(state)
                    index += 1
                else:
                    state = None
        elif state == "bracket-identifier":
            if character == "]":
                state = None
        elif character == "-" and index + 1 < len(sql) and sql[index + 1] == "-":
            buffer.append("-")
            index += 1
            state = "line-comment"
        elif character == "/" and index + 1 < len(sql) and sql[index + 1] == "*":
            buffer.append("*")
            index += 1
            state = "block-comment"
        elif character in {"'", '"', "`"}:
            state = character
        elif character == "[":
            state = "bracket-identifier"
        elif character == ";" and sqlite3.complete_statement("".join(buffer)):
            statement = "".join(buffer).strip()
            if statement:
                yield statement
            buffer.clear()

        index += 1

    statement = "".join(buffer).strip()
    if statement:
        yield statement
