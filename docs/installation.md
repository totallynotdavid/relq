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

## Engine versions

relq compiles for two fixed dialects, and each has a minimum server version.
Both floors are older than every release either project still supports:

| Engine | Minimum | What sets it |
| --- | --- | --- |
| SQLite | 3.35.0 (2021-03-12) | `RETURNING` and CTE `AS MATERIALIZED` both arrived in this release, so a build that rejects one rejects the other. Window frame `EXCLUDE` and `GROUPS` need 3.28.0. |
| PostgreSQL | 12 (2019-10-03) | CTE `AS MATERIALIZED`. Window frame `EXCLUDE` and `GROUPS` need 11; everything else relq emits is older still. |

These are a documented contract, not a runtime gate. relq's compiler never sees
a connection (it takes a query and returns SQL), so it cannot check a server's
version on your behalf, and adding a version probe to every statement would put
a round trip in the executor's hot path. Pin your engine at or above these
versions in deployment.
