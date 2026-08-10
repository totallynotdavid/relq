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

PostgreSQL codegen additionally handles enums (rendered as `enum.StrEnum`,
including arrays), domains, `inet` address/interface unions, and array columns,
decoded through the core `list_decoder` so `int[]` stays a truthful `list[int]`,
not a string.

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
