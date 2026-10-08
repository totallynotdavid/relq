"""CLI for deterministic relq schema module generation."""

import argparse
import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from relq_codegen.config import DEFAULT_ATTRIBUTE, load_config
from relq_codegen.errors import CodegenError
from relq_codegen.generate import generate_postgres, generate_sqlite, write_module
from relq_codegen.model import CodegenConfig


@dataclass(frozen=True, slots=True)
class _SqliteArguments:
    database: Path
    output: Path
    check: bool
    config: str | None
    exclude_tables: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PostgresArguments:
    dsn: str
    output: Path
    schema: str
    check: bool
    config: str | None
    exclude_tables: tuple[str, ...]


def main() -> None:
    parser = argparse.ArgumentParser(prog="relq-codegen")
    subcommands = parser.add_subparsers(dest="dialect", required=True)
    sqlite_parser = subcommands.add_parser("sqlite", help="generate from a SQLite database")
    sqlite_parser.add_argument("database", type=Path)
    sqlite_parser.add_argument("output", type=Path)
    postgres_parser = subcommands.add_parser("postgres", help="generate from a PostgreSQL database")
    postgres_parser.add_argument("dsn")
    postgres_parser.add_argument("output", type=Path)
    postgres_parser.add_argument("--schema", default="public")
    for subparser in (sqlite_parser, postgres_parser):
        subparser.add_argument(
            "--check", action="store_true", help="fail if the output module is stale"
        )
        subparser.add_argument(
            "--config",
            help=(
                "type mapping: a .toml file, or a Python module 'package.module[:NAME]' "
                f"defining a CodegenConfig (default NAME: {DEFAULT_ATTRIBUTE})"
            ),
        )
        subparser.add_argument(
            "--exclude-table",
            action="append",
            default=[],
            metavar="NAME",
            help="leave a table out of the module, such as the relq-migrate history table",
        )
    arguments = _validated_arguments(parser.parse_args())
    try:
        config = load_config(arguments.config) if arguments.config is not None else None
        if isinstance(arguments, _SqliteArguments):
            connection = sqlite3.connect(
                f"{arguments.database.absolute().as_uri()}?mode=ro", uri=True
            )
            try:
                generated = generate_sqlite(
                    connection, config=config, exclude_tables=arguments.exclude_tables
                )
            finally:
                connection.close()
        else:
            generated = asyncio.run(_generate_postgres(arguments, config))
        write_module(arguments.output, generated, check=arguments.check)
    except (CodegenError, OSError, sqlite3.Error) as error:
        message = " ".join(str(error).split()) or type(error).__name__
        raise SystemExit(f"relq-codegen: {message}") from error


def _validated_arguments(arguments: argparse.Namespace) -> _SqliteArguments | _PostgresArguments:
    """Narrow argparse's untyped namespace to one of the typed argument shapes."""
    dialect = getattr(arguments, "dialect", None)
    output = getattr(arguments, "output", None)
    check = getattr(arguments, "check", None)
    config = getattr(arguments, "config", None)
    excluded = getattr(arguments, "exclude_table", None)
    if (
        not isinstance(output, Path)
        or not isinstance(check, bool)
        or not (config is None or isinstance(config, str))
        or not isinstance(excluded, list)
        or not all(isinstance(name, str) for name in cast(list[object], excluded))
    ):
        raise TypeError("invalid relq-codegen arguments")
    exclude_tables = tuple(cast(list[str], excluded))
    if dialect == "sqlite":
        database = getattr(arguments, "database", None)
        if isinstance(database, Path):
            return _SqliteArguments(database, output, check, config, exclude_tables)
    elif dialect == "postgres":
        dsn = getattr(arguments, "dsn", None)
        schema = getattr(arguments, "schema", None)
        if isinstance(dsn, str) and isinstance(schema, str):
            return _PostgresArguments(dsn, output, schema, check, config, exclude_tables)
    raise TypeError("invalid relq-codegen arguments")


async def _generate_postgres(arguments: _PostgresArguments, config: CodegenConfig | None) -> str:
    try:
        import asyncpg
    except ModuleNotFoundError as error:
        raise SystemExit(
            "PostgreSQL generation requires `pip install 'relq-codegen[postgres]'`."
        ) from error
    try:
        connection = await asyncpg.connect(arguments.dsn)
    except (OSError, ValueError, asyncpg.PostgresError, asyncpg.InterfaceError) as error:
        # A malformed DSN is a ValueError (a bad port) or a ClientConfigurationError.
        raise CodegenError(f"cannot connect to PostgreSQL: {error}") from error
    try:
        return await generate_postgres(
            connection,
            schema=arguments.schema,
            config=config,
            exclude_tables=arguments.exclude_tables,
        )
    except (asyncpg.PostgresError, asyncpg.InterfaceError) as error:
        raise CodegenError(f"cannot read the PostgreSQL schema: {error}") from error
    finally:
        await connection.close()


if __name__ == "__main__":
    main()
