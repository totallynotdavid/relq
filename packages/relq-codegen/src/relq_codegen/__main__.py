"""CLI for deterministic relq schema module generation."""

import argparse
import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from relq_codegen import generate_postgres, generate_sqlite


@dataclass(frozen=True, slots=True)
class _SqliteArguments:
    database: Path
    output: Path
    check: bool


@dataclass(frozen=True, slots=True)
class _PostgresArguments:
    dsn: str
    output: Path
    schema: str
    check: bool


def main() -> None:
    parser = argparse.ArgumentParser(prog="relq-codegen")
    subcommands = parser.add_subparsers(dest="dialect", required=True)
    sqlite_parser = subcommands.add_parser("sqlite", help="generate from a SQLite database")
    sqlite_parser.add_argument("database", type=Path)
    sqlite_parser.add_argument("output", type=Path)
    sqlite_parser.add_argument("--check", action="store_true", help="fail if output is stale")
    postgres_parser = subcommands.add_parser("postgres", help="generate from a PostgreSQL database")
    postgres_parser.add_argument("dsn")
    postgres_parser.add_argument("output", type=Path)
    postgres_parser.add_argument("--schema", default="public")
    postgres_parser.add_argument("--check", action="store_true", help="fail if output is stale")
    arguments = _validated_arguments(parser.parse_args())
    if isinstance(arguments, _SqliteArguments):
        connection = sqlite3.connect(arguments.database)
        try:
            generated = generate_sqlite(connection)
        finally:
            connection.close()
    else:
        generated = asyncio.run(_generate_postgres(arguments))
    if arguments.check:
        if not arguments.output.exists() or arguments.output.read_text() != generated:
            raise SystemExit(f"generated schema is stale: {arguments.output}")
        return
    _write_if_changed(arguments.output, generated)


def _validated_arguments(arguments: argparse.Namespace) -> _SqliteArguments | _PostgresArguments:
    """Turn argparse's dynamic namespace into a checked, typed command shape."""
    dialect = getattr(arguments, "dialect", None)
    output = getattr(arguments, "output", None)
    check = getattr(arguments, "check", None)
    if not isinstance(output, Path) or not isinstance(check, bool):
        raise TypeError("invalid relq-codegen arguments")
    if dialect == "sqlite":
        database = getattr(arguments, "database", None)
        if isinstance(database, Path):
            return _SqliteArguments(database, output, check)
    elif dialect == "postgres":
        dsn = getattr(arguments, "dsn", None)
        schema = getattr(arguments, "schema", None)
        if isinstance(dsn, str) and isinstance(schema, str):
            return _PostgresArguments(dsn, output, schema, check)
    raise TypeError("invalid relq-codegen arguments")


def _write_if_changed(output: Path, generated: str) -> None:
    """Avoid needless timestamp-only diffs when a migration changes nothing."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_text() == generated:
        return
    output.write_text(generated)


async def _generate_postgres(arguments: _PostgresArguments) -> str:
    try:
        import asyncpg
    except ModuleNotFoundError as error:
        raise SystemExit(
            "PostgreSQL generation requires `pip install 'relq-codegen[postgres]'`."
        ) from error
    connection = await asyncpg.connect(arguments.dsn)
    try:
        return await generate_postgres(connection, schema=arguments.schema)
    finally:
        await connection.close()


if __name__ == "__main__":
    main()
