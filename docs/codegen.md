# Code generation

`relq-codegen` reads database metadata and renders a deterministic Python module
that you commit. The module holds table declarations, DML payload contracts, row
models, and typed helpers. It is a development tool, not a runtime dependency.
Run it with `uv tool run` from the application repository.

```bash
uv tool run relq-codegen sqlite path/to/app.db src/my_app/db_schema.py
uv tool run "relq-codegen[postgres]" postgres "$DATABASE_URL" src/my_app/db_schema.py --schema public
```

Regenerate after every migration and commit the result with it. `--check` fails
instead of writing, which suits CI and migration verification:

```bash
uv tool run relq-codegen sqlite path/to/app.db src/my_app/db_schema.py --check
```

The CLI writes only when the content changed, so `--check` fails exactly when
the schema and the module disagree.

## What gets generated

For each table:

- A `Table` subclass and a bound instance.
- `TableNameInsert` and `TableNameUpdate` `TypedDict` payload contracts.
- The helpers `insert_table_name(payload)`, `insert_table_name_many(payloads)`,
  and `update_table_name(payload)`.
- A frozen `TableNameRow` dataclass and a `table_name_row_adapter` that decodes
  raw tuples into it.

An insert field is required only when introspection shows that the column is
non-nullable and has no default, identity, generated, or primary-key behavior.
Every other insert field is optional. Generated columns cannot be written, so
neither payload type includes them.

PostgreSQL codegen binds each table to the schema it was introspected from. A
module generated with `--schema rqueue` declares `Jobs("jobs", schema="rqueue")`
and compiles to `"rqueue"."jobs"`, so the module never resolves through the
connection's `search_path`. A module generated from `public` names it too. One
run covers one schema. See [Design boundaries](./design-boundaries.md).

A `json` or `jsonb` column is generated as `Column[JsonValue]` through
`json_column()`, which matches what `json_decoder()` produces and works with
`relq.postgres.json_text`. `column(JsonValue)` does not type-check because
`JsonValue` is a recursive type alias, which a type checker rejects as a
`TypeForm` value.

PostgreSQL codegen also handles enums (rendered as `enum.StrEnum`, arrays
included), domains, `inet` address and interface unions, and array columns. Array
columns decode through the core `list_decoder`, so `int[]` is a `list[int]`.

## Generated names never shadow what the module imports

A generated module imports names it then depends on: `Column`, `column`,
`json_column`, `JsonValue`, the decoders, and whatever a `CodegenConfig` type
mapping brings in. A database can contain a table called `json_value` or a
column called `column`. Codegen therefore allocates every declaration in one
pass against that import namespace before it renders anything.

Names that come from the database are renamed deterministically, because you
cannot change them without a migration. A colliding column becomes
`json_column_` and keeps its SQL name through `name="json_column"`. A colliding
class becomes `JsonValue_`, and its instance, `Insert`, `Update`, and `Row`
names change with it. Two tables that normalize to the same Python name are
separated the same way.

A `GeneratedWrapper` name is rejected with a codegen-time error instead. You
chose that name, so generating a different one would be worse than refusing.

Python's `__dunder__` namespace is reserved by shape, not by a list of names. A
table called `__all__` would be replaced by the module's export list, and a
column called `__annotations__` would be collected as a dataclass field with a
dict default. A dunder-shaped name loses its leading underscores and keeps its
SQL name through `name=`.

The reserved set is derived. It is the set of imports the renderer and the type
mapper can emit, and both refuse to emit an import they have not declared.
Rendering then asserts that every name the generated source resolves through the
module namespace was one the allocation pass knew about, so a new import cannot
reintroduce a shadowing bug.

## Unknown types fail loudly

An unmapped SQL type fails generation instead of becoming `str`. Pass a
`CodegenConfig` mapping to cover a domain or type that codegen does not
recognize.

## Formatting

The renderer produces one fixed source layout and does not run an external
formatter. Output therefore does not depend on the formatter an application uses,
and `--check` stays exact. Exclude generated modules from your formatter, or
regenerate after the formatter changes them.
