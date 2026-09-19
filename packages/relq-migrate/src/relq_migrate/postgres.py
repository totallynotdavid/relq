"""Asynchronous PostgreSQL migration adapter."""

from __future__ import annotations

import logging
import weakref
import zlib
from collections.abc import Iterator
from typing import TYPE_CHECKING, TypeIs, cast

if TYPE_CHECKING:
    import asyncpg

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

if TYPE_CHECKING:
    type _Connection = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy[asyncpg.Record]
    type _Pool = asyncpg.Pool
else:
    type _Connection = object
    type _Pool = object
_LOCK_NAMESPACE = 0x52454C51
_POSTGRES_IDENTIFIER_MAX_BYTES = 63
_LOGGER = logging.getLogger(__name__)
_SESSION_SCHEMA_CACHE: weakref.WeakKeyDictionary[object, dict[str, str]] = (
    weakref.WeakKeyDictionary()
)


class PostgresMigrator:
    """Apply filesystem migrations through asyncpg.

    The advisory lock is session-scoped and held across each migration's own
    transaction. Earlier successful migrations stay committed if a later file
    fails, and no other migrator can observe or apply the same pending file
    concurrently.
    """

    def __init__(
        self,
        connection: _Connection | _Pool,
        provider: FileMigrationProvider,
        *,
        table_name: str = "relq_migrations",
    ) -> None:
        self._connection = connection
        self._provider = provider
        self._table_name = validate_identifier(
            table_name,
            kind="PostgreSQL history table",
            max_bytes=_POSTGRES_IDENTIFIER_MAX_BYTES,
        )

    async def migrate_to_latest(self) -> MigrationReport:
        if _is_pool(self._connection):
            async with self._connection.acquire() as connection:
                return await self._migrate_connection(connection)
        return await self._migrate_connection(self._connection)

    async def _migrate_connection(self, connection: _Connection) -> MigrationReport:
        if connection.is_in_transaction():
            raise RuntimeError(
                "PostgresMigrator requires a connection outside a caller-owned transaction"
            )
        locked = False
        lock_key: int | None = None
        try:
            migrations = self._provider.migrations()
            table, schema_name = await _history_table_reference(connection, self._table_name)
            lock_key = _advisory_lock_key(f"{schema_name}.{self._table_name}")
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
            applied: dict[str, str] = {}
            for row in rows:
                migration_name = cast(object, row["migration_name"])
                checksum = cast(object, row["checksum"])
                if not isinstance(migration_name, str) or not isinstance(checksum, str):
                    raise MigrationError(
                        "migration history contains a malformed row; "
                        "migration_name and checksum must be text"
                    )
                applied[migration_name] = checksum
            pending = pending_migrations(migrations, applied)
            results: list[MigrationResult] = []
            for index, migration in enumerate(pending):
                try:
                    async with connection.transaction():
                        transaction_id = cast(
                            int, await connection.fetchval("SELECT txid_current()")
                        )
                        # Split incrementally and re-read the session setting after each
                        # statement. The static SET/RESET checks are only an early
                        # diagnostic.
                        remaining_sql = migration.sql
                        while remaining_sql:
                            standard_conforming_strings = await _standard_conforming_strings(
                                connection
                            )
                            next_statement = next(
                                _iter_postgres_statements(
                                    remaining_sql,
                                    standard_conforming_strings=standard_conforming_strings,
                                ),
                                None,
                            )
                            if next_statement is None:
                                break
                            statement, consumed = next_statement
                            remaining_sql = remaining_sql[consumed:]
                            if _is_transaction_control_statement(statement):
                                raise MigrationError(
                                    f"migration {migration.name!r} contains transaction-control SQL"
                                )
                            if _is_standard_conforming_strings_statement(statement):
                                raise MigrationError(
                                    f"migration {migration.name!r} changes "
                                    "standard_conforming_strings, which is unsupported"
                                )
                            status = await connection.execute(statement)
                            if _is_transaction_control_status(status):
                                raise MigrationError(
                                    f"migration {migration.name!r} contains transaction-control SQL"
                                )
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
                            current_standard_conforming_strings = (
                                await _standard_conforming_strings(connection)
                            )
                            if current_standard_conforming_strings != standard_conforming_strings:
                                raise MigrationError(
                                    f"migration {migration.name!r} changes "
                                    "standard_conforming_strings, which is unsupported"
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
            if locked and lock_key is not None:
                try:
                    await connection.execute("SELECT pg_advisory_unlock($1)", lock_key)
                except Exception as error:  # noqa: BLE001 - never replace the migration report
                    _LOGGER.warning(
                        "failed to release PostgreSQL advisory lock for history table %r: %s",
                        self._table_name,
                        error,
                    )


def _is_pool(connection: object) -> TypeIs[_Pool]:
    return hasattr(connection, "acquire") and not hasattr(connection, "is_in_transaction")


async def _history_table_reference(connection: _Connection, table_name: str) -> tuple[str, str]:
    """Resolve the history table's schema, cached per physical session."""
    physical_connection = _physical_connection(connection)
    schema_cache = _SESSION_SCHEMA_CACHE.setdefault(physical_connection, {})
    schema_name = schema_cache.get(table_name)
    if schema_name is None:
        resolved_schema_name = cast(
            object,
            await connection.fetchval(
                """
        SELECT COALESCE(
            (
                SELECT namespace.nspname
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE relation.oid = pg_catalog.to_regclass($1)
            ),
            pg_catalog.current_schema()
        )
            """,
                quote_identifier(table_name),
            ),
        )
        if not isinstance(resolved_schema_name, str):
            raise MigrationError("PostgreSQL did not return a schema for the migration history")
        schema_name = resolved_schema_name
        schema_cache[table_name] = schema_name
    table = _qualified_history_table(schema_name, table_name)
    return table, schema_name


def _physical_connection(connection: _Connection) -> object:
    """Return the raw connection shared by direct and pooled asyncpg handles."""
    pooled_connection = getattr(connection, "_con", None)
    return connection if pooled_connection is None else pooled_connection


async def _standard_conforming_strings(connection: _Connection) -> bool:
    value = cast(object, await connection.fetchval("SHOW standard_conforming_strings"))
    if not isinstance(value, str) or value not in {"on", "off"}:
        raise MigrationError("PostgreSQL returned an invalid standard_conforming_strings setting")
    return value == "on"


def _qualified_history_table(schema_name: str, table_name: str) -> str:
    return f"{_quote_database_identifier(schema_name)}.{quote_identifier(table_name)}"


def _quote_database_identifier(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _advisory_lock_key(table_identity: str) -> int:
    """Return a stable 64-bit lock key for one history table."""
    return (_LOCK_NAMESPACE << 32) | zlib.crc32(table_identity.encode("utf-8"))


def _is_transaction_control_status(status: str) -> bool:
    return status.upper() in {"ABORT", "BEGIN", "COMMIT", "RELEASE", "ROLLBACK", "SAVEPOINT"}


def _is_transaction_control_statement(statement: str) -> bool:
    words = tuple(_leading_keywords(statement, limit=3))
    if not words:
        return False
    if words[0] in {"ABORT", "BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}:
        return True
    if words[0] == "START":
        return len(words) > 1 and words[1] == "TRANSACTION"
    if words[0] == "PREPARE":
        return len(words) > 1 and words[1] == "TRANSACTION"
    return words[0] == "SET" and (
        (len(words) > 1 and words[1] in {"CONSTRAINTS", "TRANSACTION"})
        or (len(words) > 2 and words[1:3] == ("SESSION", "CHARACTERISTICS"))
    )


def _is_standard_conforming_strings_statement(statement: str) -> bool:
    words = tuple(_leading_keywords(statement, limit=3))
    if not words:
        return False
    if words[0] == "RESET":
        return len(words) > 1 and words[1] in {"ALL", "STANDARD_CONFORMING_STRINGS"}
    if words[0] != "SET":
        return False
    if len(words) > 1 and words[1] == "STANDARD_CONFORMING_STRINGS":
        return True
    return (
        len(words) > 2
        and words[1] in {"LOCAL", "SESSION"}
        and words[2] == ("STANDARD_CONFORMING_STRINGS")
    )


def _leading_keywords(statement: str, *, limit: int) -> Iterator[str]:
    index = 0
    words: list[str] = []
    while index < len(statement) and len(words) < limit:
        while index < len(statement):
            if statement[index].isspace():
                index += 1
            elif statement.startswith("--", index):
                newline = statement.find("\n", index + 2)
                index = len(statement) if newline == -1 else newline + 1
            elif statement.startswith("/*", index):
                index = _skip_postgres_block_comment(statement, index)
            else:
                break
        if index >= len(statement):
            break
        if statement[index] == '"':
            quoted_identifier = _read_postgres_quoted_identifier(statement, index)
            if quoted_identifier is None:
                break
            word, index = quoted_identifier
            words.append(word.upper())
            continue
        if not (statement[index].isalpha() or statement[index] == "_"):
            break
        start = index
        index += 1
        while index < len(statement) and (
            statement[index].isalnum() or statement[index] in {"_", "$"}
        ):
            index += 1
        words.append(statement[start:index].upper())
    yield from words


def _read_postgres_quoted_identifier(sql: str, start: int) -> tuple[str, int] | None:
    characters: list[str] = []
    index = start + 1
    while index < len(sql):
        if sql[index] == '"':
            if index + 1 < len(sql) and sql[index + 1] == '"':
                characters.append('"')
                index += 2
                continue
            return "".join(characters), index + 1
        characters.append(sql[index])
        index += 1
    return None


def _skip_postgres_block_comment(sql: str, start: int) -> int:
    depth = 1
    index = start + 2
    while index < len(sql) and depth:
        if sql.startswith("/*", index):
            depth += 1
            index += 2
        elif sql.startswith("*/", index):
            depth -= 1
            index += 2
        else:
            index += 1
    return index


def _is_postgres_identifier_character(character: str) -> bool:
    return character.isalnum() or character in {"_", "$"}


def _dollar_quote_delimiter(sql: str, index: int) -> str | None:
    if sql[index] != "$" or (index > 0 and _is_postgres_identifier_character(sql[index - 1])):
        return None
    end = sql.find("$", index + 1)
    if end == -1:
        return None
    tag = sql[index + 1 : end]
    if tag and not (tag[0].isalpha() or tag[0] == "_"):
        return None
    if any(not (character.isalnum() or character == "_") for character in tag):
        return None
    return sql[index : end + 1]


def _is_escape_string(sql: str, quote_index: int, *, standard_conforming_strings: bool) -> bool:
    if not standard_conforming_strings:
        return True
    prefix = quote_index - 1
    if prefix < 0 or sql[prefix] not in {"e", "E"}:
        return False
    return prefix == 0 or not _is_postgres_identifier_character(sql[prefix - 1])


def _postgres_keyword_at(sql: str, index: int) -> tuple[str, int] | None:
    if index >= len(sql) or not (sql[index].isalpha() or sql[index] == "_"):
        return None
    end = index + 1
    while end < len(sql) and (sql[end].isalnum() or sql[end] in {"_", "$"}):
        end += 1
    return sql[index:end].upper(), end


def _next_postgres_keyword(sql: str, index: int) -> str | None:
    while index < len(sql):
        if sql[index].isspace():
            index += 1
        elif sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = len(sql) if newline == -1 else newline + 1
        elif sql.startswith("/*", index):
            index = _skip_postgres_block_comment(sql, index)
        else:
            token = _postgres_keyword_at(sql, index)
            return None if token is None else token[0]
    return None


def _iter_postgres_statements(
    sql: str, *, standard_conforming_strings: bool = True
) -> Iterator[tuple[str, int]]:
    """Yield each statement with the number of characters consumed through it.

    Semicolons inside quoted text and atomic blocks do not end a statement.
    """
    buffer: list[str] = []
    state = "normal"
    block_depth = 0
    dollar_delimiter: str | None = None
    escape_string = False
    atomic_blocks: list[str] = []
    ignored_end_suffix: str | None = None
    index = 0
    while index < len(sql):
        character = sql[index]
        if state == "normal":
            if sql.startswith("--", index):
                buffer.extend(("-", "-"))
                state = "line-comment"
                index += 2
            elif sql.startswith("/*", index):
                buffer.extend(("/", "*"))
                state = "block-comment"
                block_depth = 1
                index += 2
            elif character == "'":
                buffer.append(character)
                state = "single-quote"
                escape_string = _is_escape_string(
                    sql, index, standard_conforming_strings=standard_conforming_strings
                )
                index += 1
            elif character == '"':
                buffer.append(character)
                state = "double-quote"
                index += 1
            elif character == "$" and (
                (dollar_delimiter := _dollar_quote_delimiter(sql, index)) is not None
            ):
                buffer.extend(dollar_delimiter)
                state = "dollar-quote"
                index += len(dollar_delimiter)
            else:
                token = _postgres_keyword_at(sql, index)
                if token is not None:
                    keyword, next_index = token
                    if ignored_end_suffix == keyword:
                        ignored_end_suffix = None
                    elif atomic_blocks:
                        if keyword == "BEGIN":
                            atomic_blocks.append("BEGIN")
                        elif keyword == "CASE":
                            atomic_blocks.append("CASE")
                        elif keyword == "END":
                            atomic_blocks.pop()
                            next_keyword = _next_postgres_keyword(sql, next_index)
                            if next_keyword in {"CASE", "IF", "LOOP", "WHILE"}:
                                ignored_end_suffix = next_keyword
                    elif keyword == "BEGIN" and _next_postgres_keyword(sql, next_index) == "ATOMIC":
                        atomic_blocks.append("BEGIN")
                    buffer.extend(sql[index:next_index])
                    index = next_index
                    continue
                buffer.append(character)
                index += 1
                if character == ";" and not atomic_blocks:
                    statement = "".join(buffer).strip()
                    if statement and not is_sql_empty(statement):
                        yield statement, index
                    buffer.clear()
        elif state == "line-comment":
            buffer.append(character)
            index += 1
            if character in {"\r", "\n"}:
                state = "normal"
        elif state == "block-comment":
            if sql.startswith("/*", index):
                buffer.extend(("/", "*"))
                block_depth += 1
                index += 2
            elif sql.startswith("*/", index):
                buffer.extend(("*", "/"))
                block_depth -= 1
                index += 2
                if block_depth == 0:
                    state = "normal"
            else:
                buffer.append(character)
                index += 1
        elif state == "single-quote":
            if character == "\\" and escape_string:
                buffer.append(character)
                index += 1
                if index < len(sql):
                    buffer.append(sql[index])
                    index += 1
            elif character == "'":
                buffer.append(character)
                index += 1
                if index < len(sql) and sql[index] == "'":
                    buffer.append(sql[index])
                    index += 1
                else:
                    state = "normal"
            else:
                buffer.append(character)
                index += 1
        elif state == "double-quote":
            buffer.append(character)
            index += 1
            if character == '"':
                if index < len(sql) and sql[index] == '"':
                    buffer.append(sql[index])
                    index += 1
                else:
                    state = "normal"
        else:
            assert dollar_delimiter is not None
            if sql.startswith(dollar_delimiter, index):
                buffer.extend(dollar_delimiter)
                index += len(dollar_delimiter)
                state = "normal"
            else:
                buffer.append(character)
                index += 1

    statement = "".join(buffer).strip()
    if statement and not is_sql_empty(statement):
        yield statement, len(sql)


def _split_postgres_statements(  # pyright: ignore[reportUnusedFunction]
    sql: str, *, standard_conforming_strings: bool = True
) -> Iterator[str]:
    """Split PostgreSQL SQL into statements, discarding the offsets."""
    for statement, _ in _iter_postgres_statements(
        sql, standard_conforming_strings=standard_conforming_strings
    ):
        yield statement
