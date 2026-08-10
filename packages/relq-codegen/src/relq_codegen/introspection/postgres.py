"""PostgreSQL catalog adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from relq_codegen.model import (
    ArrayType,
    BuiltinType,
    NamedType,
    SchemaColumn,
    SchemaEnum,
    SchemaTable,
    SqlType,
    TypeIdentity,
)

if TYPE_CHECKING:
    from asyncpg import Connection


class _PostgresColumn(NamedTuple):
    table_schema: str
    table_name: str
    column_name: str
    type_name: str
    type_schema: str
    type_kind: str
    element_type_name: str | None
    element_type_schema: str | None
    element_type_kind: str | None
    nullable: bool
    primary_key: bool
    has_default: bool
    identity: str
    generated: str


class _PostgresEnum(NamedTuple):
    schema: str
    name: str
    labels: list[str]


async def inspect_postgres(
    connection: Connection, *, schema: str = "public"
) -> tuple[SchemaTable, ...]:
    """Read columns from PostgreSQL catalogs without discarding type identity."""
    statement = """
        select table_ns.nspname as table_schema, table_class.relname as table_name,
               attribute.attname as column_name, column_type.typname as type_name,
               type_ns.nspname as type_schema, column_type.typtype::text as type_kind,
               element_type.typname as element_type_name,
               element_type_ns.nspname as element_type_schema,
               element_type.typtype::text as element_type_kind,
               not attribute.attnotnull as nullable,
               exists (
                   select 1 from pg_index index
                   where index.indrelid = table_class.oid and index.indisprimary
                     and attribute.attnum = any(index.indkey)
               ) as primary_key,
               attribute.atthasdef as has_default,
               attribute.attidentity::text as identity, attribute.attgenerated::text as generated
        from pg_attribute attribute
        join pg_class table_class on table_class.oid = attribute.attrelid
        join pg_namespace table_ns on table_ns.oid = table_class.relnamespace
        join pg_type column_type on column_type.oid = attribute.atttypid
        join pg_namespace type_ns on type_ns.oid = column_type.typnamespace
        left join pg_type element_type on element_type.oid = column_type.typelem
        left join pg_namespace element_type_ns on element_type_ns.oid = element_type.typnamespace
        where table_ns.nspname = $1
          and table_class.relkind in ('r', 'p')
          and attribute.attnum > 0 and not attribute.attisdropped
        order by table_class.relname, attribute.attnum
    """
    rows = await connection.fetch(statement, schema)
    by_table: dict[tuple[str, str], list[SchemaColumn]] = {}
    for record in rows:
        row = _PostgresColumn(*record)
        by_table.setdefault((row.table_schema, row.table_name), []).append(
            SchemaColumn(
                name=row.column_name,
                sql_type=_postgres_type(row),
                nullable=row.nullable,
                primary_key=row.primary_key,
                has_default=row.has_default,
                identity=bool(row.identity),
                generated=bool(row.generated),
            )
        )
    return tuple(
        SchemaTable(name, tuple(columns), table_schema)
        for (table_schema, name), columns in by_table.items()
    )


async def inspect_postgres_enums(
    connection: Connection, *, schema: str = "public"
) -> tuple[SchemaEnum, ...]:
    statement = """
        select namespace.nspname as schema, type.typname as name,
               array_agg(value.enumlabel order by value.enumsortorder) as labels
        from pg_type type
        join pg_namespace namespace on namespace.oid = type.typnamespace
        join pg_enum value on value.enumtypid = type.oid
        where namespace.nspname = $1 and type.typtype = 'e'
        group by namespace.nspname, type.typname
        order by namespace.nspname, type.typname
    """
    rows = await connection.fetch(statement, schema)
    return tuple(
        SchemaEnum(TypeIdentity(row.schema, row.name), tuple(row.labels))
        for row in (_PostgresEnum(*record) for record in rows)
    )


def _postgres_type(row: _PostgresColumn) -> SqlType:
    if row.element_type_name is not None:
        return ArrayType(
            _postgres_named_or_builtin(
                row.element_type_kind, row.element_type_schema, row.element_type_name
            )
        )
    return _postgres_named_or_builtin(row.type_kind, row.type_schema, row.type_name)


def _postgres_named_or_builtin(kind: str | None, schema: str | None, name: str) -> SqlType:
    if kind == "e":
        return NamedType("enum", TypeIdentity(schema, name))
    if kind == "d":
        return NamedType("domain", TypeIdentity(schema, name))
    return BuiltinType(name)
