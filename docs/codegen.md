# Code generation

`relq-codegen` reads database metadata and renders a deterministic Python module
that you commit. The module holds table declarations, DML payload contracts, row
models, and typed helpers. It is a development tool, not a runtime dependency.
Run it with `uv tool run` from the application repository:

```bash
uv tool run relq-codegen sqlite path/to/app.db src/my_app/db_schema.py
uv tool run "relq-codegen[postgres]" postgres "$DATABASE_URL" src/my_app/db_schema.py --schema public
```

Regenerate after every migration and commit the result with it.
[`relq-migrate --codegen`](./migrations.md#command-line) does both in one
command. `--check` writes nothing and exits with an error when the file is
missing or differs from what generation would produce. It suits CI:

```bash
uv tool run relq-codegen sqlite path/to/app.db src/my_app/db_schema.py --check
```

| Option                 | Meaning                                                                    |
| ---------------------- | -------------------------------------------------------------------------- |
| `--schema NAME`        | PostgreSQL schema to generate from (default `public`).                     |
| `--check`              | Write nothing. Fail when the module is missing or stale.                   |
| `--config REFERENCE`   | Type mapping: a `.toml` file, or a Python module. See [Types](#types).     |
| `--exclude-table NAME` | Leave a table out, such as the `relq_migrations` history table. Repeat it. |

A failure it expects prints one line, `relq-codegen: <message>`, and exits
non-zero. These are a schema or type-mapping problem, an unreadable database,
config or output file, and a PostgreSQL URL that is malformed or cannot be
reached. A wrong option prints the usage message instead. The SQLite database is
opened read-only, so a wrong path is an error and never creates an empty
database.

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
run covers one schema.

A `json` or `jsonb` column is generated as `Column[JsonValue] = column()`, which
matches what `json_decoder()` produces and works with `relq.postgres.json_text`.
Handwritten tables use the same spelling.

PostgreSQL codegen also handles enums (rendered as `enum.StrEnum`, arrays
included), domains, `inet` columns (typed `relq.Inet`, the union of IP address
and interface types), and array columns. Array columns decode through
`list_decoder`, so `int[]` is a `list[int]`.

## Names

A generated module imports names it depends on: `Column`, `column`, `JsonValue`,
the decoders, and whatever a `CodegenConfig` type mapping brings in. A database
can contain a table called `json_value` or a column called `column`. Codegen
renames the Python name and keeps the SQL name:

- A colliding column gets a trailing underscore, so `column` becomes `column_`
  with `name="column"`.
- A colliding table class gets a trailing underscore, so `json_value` becomes
  `JsonValue_`. Its payload and row names derive from the class name. Tables
  that normalize to the same class name get a numeric suffix.
- A name of the form `__name__` loses its leading underscores and keeps its SQL
  name through `name=`. A table `__all__` therefore does not replace the
  module's export list.

A collision with a `GeneratedWrapper` name from your configuration raises an
error instead of being renamed.

## Types

An unmapped SQL type fails generation with a one-line error instead of becoming
`str`. Map it with `--config`, either a TOML file or a Python module.

A TOML file has one `[types.<name>]` table per SQL type. A bare name matches the
type in any schema, and `schema.name` matches one schema:

```toml
[types.citext]
python = "str"
decoder = "str"

[types."public.tenant_id"]
wrapper = "TenantId"
python = "int"
decoder = "int"

[types.geometry]
reject = "the application has no value object for it yet"
```

| Key       | Meaning                                                                                                       |
| --------- | ------------------------------------------------------------------------------------------------------------- |
| `python`  | The Python type. Builtins are bare. Anything else is `module.Name`, which also imports `module`.              |
| `decoder` | One of `bool`, `bytes`, `date`, `datetime`, `decimal`, `float`, `inet`, `int`, `json`, `str`, `time`, `uuid`. |
| `wrapper` | Optional. Emit a `NewType` with this name over the type.                                                      |
| `reject`  | A reason. Generation fails on this type with that reason. No other key is allowed.                            |

```bash
uv tool run relq-codegen sqlite app.db src/my_app/db_schema.py --config relq-codegen.toml
```

A mapping that needs code, for example a custom `RenderExpression`, goes in a
Python module. Pass `package.module` or `package.module:NAME`, a dotted name
without a leading dot. The module defines a `CodegenConfig` as `CONFIG`, or as
the named attribute. The working directory is on the import path:

```python
from relq_codegen import CodegenConfig, DirectType, Name, TypeIdentity

CONFIG = CodegenConfig(
    type_mappings={TypeIdentity(None, "weird_type"): DirectType(Name("str"), "str")}
)
```

The same configuration is available from Python.
`generate_sqlite(connection, config=..., exclude_tables=...)` and the
asynchronous
`generate_postgres(connection, schema=..., config=..., exclude_tables=...)`
return the module source. `load_config(reference)` reads a `--config` value, and
`write_module(output, source, check=...)` writes the file with the same
freshness rule as the CLI. Failures raise `CodegenError`.

## Formatting

The renderer lays out imports the way Ruff and Black do: one statement per
module, standard library first, long lists wrapped. `--check` compares syntax
trees, not text. A module that your formatter has rewritten is still current,
and codegen leaves it untouched. Any change to a name, type, or statement makes
it stale.
