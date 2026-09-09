# Code generation

`relq-codegen` reads database metadata and renders a deterministic, committed
Python module: table declarations, DML payload contracts, and typed helpers.
It's a development tool, not a runtime dependency. Install and run it with
`uv tool run` from the application repository, not from a checkout of relq.

```bash
uv tool run relq-codegen sqlite path/to/app.db src/my_app/db_schema.py
uv tool run "relq-codegen[postgres]" postgres "$DATABASE_URL" src/my_app/db_schema.py --schema public
```

Regenerate after every migration, and commit the result alongside it. `--check`
fails instead of writing, for use in CI or migration verification:

```bash
uv tool run relq-codegen sqlite path/to/app.db src/my_app/db_schema.py --check
```

The CLI only writes when content actually changed, so `--check` is a precise
schema-change gate rather than a timestamp diff.

## What gets generated

For each table: a `Table` subclass, a bound instance, `TableNameInsert` /
`TableNameUpdate` `TypedDict` payload contracts, and
`insert_table_name(payload)`, `insert_table_name_many(payloads)`,
`update_table_name(payload)` helpers. An insert field is required only when
introspection proves it's non-nullable and has no default, identity, generated,
or primary-key behavior; nullable, defaulted, identity, and primary-key fields
are optional. Generated columns can't be written, so codegen omits them from
both payload types.

PostgreSQL codegen binds each table to the schema it was introspected from, so
a module generated with `--schema rqueue` declares `Jobs("jobs",
schema="rqueue")` and compiles to `"rqueue"."jobs"`. That keeps the generated
module pinned to the namespace it describes instead of silently resolving
through the connection's `search_path`; a module generated from `public` says
so too. One run still covers one schema: see
[Design boundaries](./design-boundaries.md).

A `json`/`jsonb` column is generated as `Column[JsonValue]` via `json_column()`,
matching what `json_decoder()` actually produces, so generated columns work with
`relq.postgres.json_text`. (`column(JsonValue)` cannot be written: `JsonValue` is
a recursive type alias, which a type checker will not accept as the `TypeForm`
value `column` infers its result from.)

PostgreSQL codegen additionally handles enums (rendered as `enum.StrEnum`,
including arrays), domains, `inet` address/interface unions, and array columns,
decoded through the core `list_decoder` so `int[]` stays a truthful `list[int]`,
not a string.

## Generated names never shadow what the module imports

A generated module imports names it then depends on: `Column`, `column`,
`json_column`, `JsonValue`, the decoders, and whatever a `CodegenConfig` type
mapping brings in. A database is free to contain a table called `json_value` or
a column called `column`, so codegen allocates every declaration it emits
against that import namespace in a single pass before rendering anything.

Names that come from the database are renamed, deterministically, because you
can't change them without a migration: a colliding column becomes
`json_column_` and keeps its SQL name through `name='json_column'`, and a
colliding class becomes `JsonValue_` with its instance and `…Insert` / `…Update`
/ `…Row` family renamed with it. Two tables that normalize to the same Python
name are separated the same way.

A `GeneratedWrapper` name is rejected instead, with a codegen-time error: you
chose that name, so quietly generating a different one would be worse than
refusing.

Python's `__dunder__` namespace is reserved by shape, not by listing names: a
table called `__all__` would be replaced by the module's export list and a
column called `__annotations__` would be collected as a dataclass field with a
dict default, so a dunder-shaped name loses its leading underscores and keeps
its SQL name through `name=`.

The reserved set is derived, not maintained by hand. It is the set of imports
the renderer and the type mapper can actually emit, and both refuse to emit an
import they haven't declared. Rendering then asserts that every name the
generated source resolves through the module namespace was one the pass knew
about, so a new import cannot reintroduce a shadowing bug.

## Unknown types fail loudly

An unmapped SQL type fails generation rather than silently becoming `str`. Pass
an explicit `CodegenConfig` mapping to cover a domain or type codegen doesn't
recognize.

## Formatting

The renderer owns a canonical, deterministic source layout, but deliberately
does not invoke an external formatter. That keeps generation reproducible
without making an application's formatter a runtime dependency of codegen, and
keeps `--check` exact. Treat generated modules as committed artifacts: exclude
them from your own formatter's scope, or regenerate after your formatter changes
them.
