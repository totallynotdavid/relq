"""Public code-generation orchestration."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from relq_codegen.introspection import inspect_postgres, inspect_postgres_enums, inspect_sqlite
from relq_codegen.model import CodegenConfig
from relq_codegen.render import render

if TYPE_CHECKING:
    from asyncpg import Connection


def generate_sqlite(connection: sqlite3.Connection, *, config: CodegenConfig | None = None) -> str:
    return render(inspect_sqlite(connection), config=config)


async def generate_postgres(
    connection: Connection, *, schema: str = "public", config: CodegenConfig | None = None
) -> str:
    return render(
        await inspect_postgres(connection, schema=schema),
        enums=await inspect_postgres_enums(connection, schema=schema),
        config=config,
    )
