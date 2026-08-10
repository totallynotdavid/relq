"""SQLite catalog adapter."""

import sqlite3
from typing import cast

from relq_codegen.model import BuiltinType, SchemaColumn, SchemaTable


def inspect_sqlite(connection: sqlite3.Connection) -> tuple[SchemaTable, ...]:
    """Read user tables and columns using SQLite's stable PRAGMA metadata."""
    names = _rows(
        connection.execute(
            "select name from sqlite_master where type = 'table' and name not like 'sqlite_%' order by name"
        )
    )
    tables: list[SchemaTable] = []
    for (name,) in names:
        table_name = str(name)
        escaped = table_name.replace('"', '""')
        rows = _rows(connection.execute(f'pragma table_xinfo("{escaped}")'))
        columns = tuple(
            SchemaColumn(
                name=str(row[1]),
                sql_type=BuiltinType(str(row[2])),
                nullable=not bool(row[3]) and not bool(row[5]),
                primary_key=bool(row[5]),
                has_default=row[4] is not None,
                generated=bool(row[6]),
            )
            for row in rows
        )
        tables.append(SchemaTable(table_name, columns))
    return tuple(tables)


def _rows(cursor: sqlite3.Cursor) -> tuple[tuple[object, ...], ...]:
    """Normalize SQLite's unparameterized DB-API result boundary once."""
    rows: list[tuple[object, ...]] = []
    for index, raw_row in enumerate(_list_items(cast(object, cursor.fetchall()))):
        # sqlite3 types fetched rows as ``Any``.  Do not let that leak from
        # introspection into normalized catalog metadata.
        row = raw_row
        if not isinstance(row, tuple):
            raise TypeError(f"SQLite cursor returned a non-tuple row at index {index}")
        rows.append(_tuple_items(cast(tuple[object, ...], row)))
    return tuple(rows)


def _list_items(value: object) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise TypeError("SQLite cursor returned a non-list result")
    return tuple(cast(list[object], value))


def _tuple_items(value: tuple[object, ...]) -> tuple[object, ...]:
    return value
