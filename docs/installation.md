# Installation

relq installs the driver your application uses:

```bash
uv add "relq[sqlite]"
# or
uv add "relq[postgres]"
# or both
uv add "relq[sqlite,postgres]"
```

`relq` itself has no dependencies. The `sqlite` extra pins `relq-sqlite`, which
wraps the standard library's `sqlite3`. The `postgres` extra pins
`relq-postgres`, which wraps `asyncpg`. Both extras pin an exact version of
their executor.

Schema generation is a separate, independent development tool. Run it with
`uv tool run` from the application repository, not from a checkout of relq:

```bash
uv tool run relq-codegen sqlite path/to/app.db src/my_app/db_schema.py
uv tool run "relq-codegen[postgres]" postgres "$DATABASE_URL" src/my_app/db_schema.py
```

`relq-codegen`'s SQLite path is dependency-free as well; its PostgreSQL path
imports `asyncpg` only when the `postgres` extra is installed and used. See
[Code generation](./codegen.md).

relq targets Python 3.15+ only.
