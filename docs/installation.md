# Installation

relq installs the driver your application uses:

```bash
uv add "relq[sqlite]"
# or
uv add "relq[postgres]"
# or both
uv add "relq[sqlite,postgres]"
```

Schema migrations are provided by the separate `relq-migrate` package:

```bash
uv add relq-migrate
# For PostgreSQL migrations:
uv add "relq-migrate[postgres]"
```

`relq` itself depends only on `typing-extensions`, which provides the `TypeForm`
annotation that column declarations use. The `sqlite` extra pins `relq-sqlite`,
which wraps the standard library's `sqlite3`. The `postgres` extra pins
`relq-postgres`, which wraps `asyncpg`. Each extra pins an exact executor
version.

`relq-migrate` has no dependencies for SQLite. Install its `postgres` extra only
for the asynchronous PostgreSQL migrator.

Schema generation is a separate development tool. Run it with `uv tool run` from
the application repository, not from a checkout of relq:

```bash
uv tool run relq-codegen sqlite path/to/app.db src/my_app/db_schema.py
uv tool run "relq-codegen[postgres]" postgres "$DATABASE_URL" src/my_app/db_schema.py
```

`relq-codegen`'s SQLite path has no dependencies either. Its PostgreSQL path
imports `asyncpg` only when the `postgres` extra is installed and used. See
[Code generation](./codegen.md).

relq requires Python 3.13 or later.

## Engine versions

relq compiles for two fixed dialects, and each has a minimum server version:

| Engine | Minimum | What sets it |
| --- | --- | --- |
| SQLite | 3.39.0 (2022-06-25) | `right_join` and `full_join` need `RIGHT`/`FULL OUTER JOIN`, which arrived in this release. `RETURNING` and CTE `AS MATERIALIZED` both need 3.35.0, so a build that rejects one rejects the other. Window frame `EXCLUDE` and `GROUPS` need 3.28.0. |
| PostgreSQL | 14 (2021-09-30) | `date_bin` needs 14. CTE `AS MATERIALIZED` needs 12. Window frame `EXCLUDE` and `GROUPS` need 11; everything else relq emits is older still. |

These minimums are a documented contract, not a runtime check. The compiler takes
a query and returns SQL without ever seeing a connection, so it cannot read the
server version, and probing the version on every statement would add a round trip
to the executor's hot path. An older server rejects the statement itself. Deploy
on an engine at or above these versions.
