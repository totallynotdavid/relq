"""CLI that migrates a database and keeps its generated schema module current."""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ._model import MigrationReport, MigrationStatus
from .postgres import PostgresMigrator
from .provider import FileMigrationProvider
from .sqlite import SQLiteMigrator

if TYPE_CHECKING:
    from asyncpg import Connection
    from relq_codegen import CodegenConfig

_PROG = "relq-migrate"


@dataclass(frozen=True, slots=True)
class _Codegen:
    output: Path
    config: str | None
    check: bool


@dataclass(frozen=True, slots=True)
class _Arguments:
    dialect: str
    database: str
    migrations: Path
    table: str
    busy_timeout: float
    schema: str
    codegen: _Codegen | None


class _Failure(Exception):
    """A failure to report as one line."""


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    arguments = _validated(parser, parser.parse_args(argv))
    try:
        config = _load_config(arguments.codegen)
        provider = FileMigrationProvider(arguments.migrations)
        if arguments.dialect == "sqlite":
            _migrate_sqlite(arguments, provider, config)
        else:
            asyncio.run(_migrate_postgres(arguments, provider, config))
    except _Failure as failure:
        raise SystemExit(f"{_PROG}: {failure}") from failure


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description=(
            "Apply the migration folder, then with --codegen regenerate the schema module "
            "from the migrated database. With --codegen --check nothing is applied or "
            "written: it fails when migrations are pending or the module is stale."
        ),
    )
    subcommands = parser.add_subparsers(dest="dialect", required=True)
    sqlite_parser = subcommands.add_parser("sqlite", help="migrate a SQLite database file")
    sqlite_parser.add_argument("database")
    sqlite_parser.add_argument(
        "--busy-timeout",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="how long to wait for another writer's lock (default: 30)",
    )
    postgres_parser = subcommands.add_parser("postgres", help="migrate a PostgreSQL database")
    postgres_parser.add_argument("database", metavar="dsn")
    postgres_parser.add_argument(
        "--schema", default="public", help="schema to generate from (default: public)"
    )
    for subparser in (sqlite_parser, postgres_parser):
        subparser.add_argument("migrations", type=Path, help="folder of NNNN_name.sql files")
        subparser.add_argument("--table", default="relq_migrations", help="history table name")
        subparser.add_argument(
            "--codegen",
            type=Path,
            metavar="OUTPUT",
            help="schema module to write after migrating (needs relq-migrate[codegen])",
        )
        subparser.add_argument(
            "--config", help="relq-codegen type mapping: a .toml file or 'package.module[:NAME]'"
        )
        subparser.add_argument(
            "--check",
            action="store_true",
            help=(
                "with --codegen, change nothing: fail if migrations are pending or OUTPUT is stale"
            ),
        )
    return parser


def _validated(parser: argparse.ArgumentParser, namespace: argparse.Namespace) -> _Arguments:
    """Narrow argparse's untyped namespace to the typed argument shape."""
    dialect = getattr(namespace, "dialect", None)
    database = getattr(namespace, "database", None)
    migrations = getattr(namespace, "migrations", None)
    table = getattr(namespace, "table", None)
    busy_timeout = getattr(namespace, "busy_timeout", 30.0)
    schema = getattr(namespace, "schema", "public")
    output = getattr(namespace, "codegen", None)
    config = getattr(namespace, "config", None)
    check = getattr(namespace, "check", None)
    if (
        not isinstance(dialect, str)
        or not isinstance(database, str)
        or not isinstance(migrations, Path)
        or not isinstance(table, str)
        or not isinstance(busy_timeout, float)
        or not isinstance(schema, str)
        or not (output is None or isinstance(output, Path))
        or not (config is None or isinstance(config, str))
        or not isinstance(check, bool)
    ):
        raise TypeError("invalid relq-migrate arguments")
    if output is None and (config is not None or check):
        parser.error("--config and --check need --codegen OUTPUT")
    codegen = None if output is None else _Codegen(output, config, check)
    return _Arguments(dialect, database, migrations, table, busy_timeout, schema, codegen)


def _load_config(codegen: _Codegen | None) -> CodegenConfig | None:
    """Fail before touching the database when codegen cannot run at all."""
    if codegen is None:
        return None
    try:
        from relq_codegen import CodegenError, load_config
    except ModuleNotFoundError as error:
        raise _Failure("--codegen needs `pip install 'relq-migrate[codegen]'`") from error
    if codegen.config is None:
        return None
    try:
        return load_config(codegen.config)
    except CodegenError as error:
        raise _Failure(f"codegen: {error}") from error


def _migrate_sqlite(
    arguments: _Arguments, provider: FileMigrationProvider, config: CodegenConfig | None
) -> None:
    checking = arguments.codegen is not None and arguments.codegen.check
    try:
        connection = _open_sqlite(arguments.database, read_only=checking)
    except sqlite3.Error as error:
        raise _Failure(f"cannot open {arguments.database}: {error}") from error
    try:
        try:
            migrator = SQLiteMigrator(
                connection,
                provider,
                table_name=arguments.table,
                busy_timeout=arguments.busy_timeout,
            )
        except ValueError as error:
            raise _Failure(str(error)) from error
        if checking:
            _raise_if_pending(migrator.pending())
        else:
            _raise_if_failed(migrator.migrate_to_latest())
        if arguments.codegen is not None:
            from relq_codegen import CodegenError, generate_sqlite, write_module

            try:
                generated = generate_sqlite(
                    connection, config=config, exclude_tables=(arguments.table,)
                )
                write_module(arguments.codegen.output, generated, check=arguments.codegen.check)
            except CodegenError as error:
                raise _Failure(f"codegen: {error}") from error
    finally:
        connection.close()


async def _migrate_postgres(
    arguments: _Arguments, provider: FileMigrationProvider, config: CodegenConfig | None
) -> None:
    try:
        import asyncpg
    except ModuleNotFoundError as error:
        raise _Failure("PostgreSQL needs `pip install 'relq-migrate[postgres]'`") from error
    checking = arguments.codegen is not None and arguments.codegen.check
    try:
        connection = await asyncpg.connect(
            arguments.database,
            server_settings={"default_transaction_read_only": "on"} if checking else None,
        )
    except (OSError, asyncpg.PostgresError) as error:
        raise _Failure(f"cannot connect to PostgreSQL: {error}") from error
    try:
        try:
            migrator = PostgresMigrator(connection, provider, table_name=arguments.table)
        except ValueError as error:
            raise _Failure(str(error)) from error
        if checking:
            _raise_if_pending(await migrator.pending())
        else:
            _raise_if_failed(await migrator.migrate_to_latest())
        if arguments.codegen is not None:
            await _regenerate_postgres(connection, arguments, arguments.codegen, config)
    finally:
        await connection.close()


async def _regenerate_postgres(
    connection: Connection, arguments: _Arguments, codegen: _Codegen, config: CodegenConfig | None
) -> None:
    from relq_codegen import CodegenError, generate_postgres, write_module

    try:
        generated = await generate_postgres(
            connection,
            schema=arguments.schema,
            config=config,
            exclude_tables=(arguments.table,),
        )
        write_module(codegen.output, generated, check=codegen.check)
    except CodegenError as error:
        raise _Failure(f"codegen: {error}") from error


def _open_sqlite(database: str, *, read_only: bool) -> sqlite3.Connection:
    """Open the database; a read-only open neither creates the file nor writes to it."""
    if not read_only:
        return sqlite3.connect(database, autocommit=True)
    return sqlite3.connect(
        f"{Path(database).absolute().as_uri()}?mode=ro", uri=True, autocommit=True
    )


def _raise_if_pending(pending: tuple[str, ...]) -> None:
    if pending:
        raise _Failure(
            f"{len(pending)} migration(s) pending ({', '.join(pending)}); "
            "run without --check to apply them"
        )


def _raise_if_failed(report: MigrationReport) -> None:
    if report.error is None:
        return
    failed = next(
        (result for result in report.results if result.status is MigrationStatus.ERROR), None
    )
    where = f"migration {failed.migration_name!r} failed: " if failed is not None else ""
    raise _Failure(f"{where}{report.error}")


if __name__ == "__main__":
    main()
