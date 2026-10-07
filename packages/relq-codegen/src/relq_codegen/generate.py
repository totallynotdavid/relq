"""Public code-generation orchestration."""

from __future__ import annotations

import ast
import sqlite3
from collections.abc import Collection
from pathlib import Path
from typing import TYPE_CHECKING

from relq_codegen.errors import CodegenError
from relq_codegen.introspection import inspect_postgres, inspect_postgres_enums, inspect_sqlite
from relq_codegen.model import CodegenConfig, SchemaTable
from relq_codegen.render import render

if TYPE_CHECKING:
    from asyncpg import Connection


def same_module(existing: str, generated: str) -> bool:
    """Whether two sources are the same program, whatever formatter laid them out.

    The renderer's layout is not the application's: ruff, black and editors
    rewrite quotes, wrapping and blank lines. Comparing syntax trees makes a
    formatted file as fresh as the text the renderer wrote, while any change to
    a name, type or statement still counts as stale.
    """
    try:
        return ast.dump(ast.parse(existing)) == ast.dump(ast.parse(generated))
    except SyntaxError:
        return False


def write_module(output: Path, generated: str, *, check: bool) -> None:
    """Write ``generated`` to ``output``, or with ``check`` verify it is current.

    A current file is left untouched, so a formatted module keeps its layout
    and its modification time.
    """
    current = output.read_text() if output.exists() else None
    if current is not None and same_module(current, generated):
        return
    if check:
        raise CodegenError(f"generated schema is stale: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generated)


def _without(tables: tuple[SchemaTable, ...], excluded: Collection[str]) -> tuple[SchemaTable, ...]:
    return tuple(table for table in tables if table.name not in excluded)


def generate_sqlite(
    connection: sqlite3.Connection,
    *,
    config: CodegenConfig | None = None,
    exclude_tables: Collection[str] = (),
) -> str:
    """Render the database's tables, except ``exclude_tables`` (such as a history table)."""
    return render(
        _without(inspect_sqlite(connection), exclude_tables), dialect="sqlite", config=config
    )


async def generate_postgres(
    connection: Connection,
    *,
    schema: str = "public",
    config: CodegenConfig | None = None,
    exclude_tables: Collection[str] = (),
) -> str:
    """Render the schema's tables, except ``exclude_tables`` (such as a history table)."""
    return render(
        _without(await inspect_postgres(connection, schema=schema), exclude_tables),
        dialect="postgres",
        enums=await inspect_postgres_enums(connection, schema=schema),
        config=config,
    )
