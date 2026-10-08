# Installation

relq requires Python 3.13 or later. Install the extra for the driver your
application uses:

```bash
uv add "relq[sqlite]"
# or
uv add "relq[postgres]"
# or both
uv add "relq[sqlite,postgres]"
```

| Package         | Install                        | Provides                                              |
| --------------- | ------------------------------ | ----------------------------------------------------- |
| `relq`          | `uv add relq`                  | Builders, expressions, compiler, and row decoders     |
| `relq-sqlite`   | the `sqlite` extra of `relq`   | `SQLiteDatabase`, on the standard library's `sqlite3` |
| `relq-postgres` | the `postgres` extra of `relq` | `PostgresDatabase`, on `asyncpg`                      |
| `relq-migrate`  | `uv add relq-migrate`          | [Migrations](./migrations.md)                         |
| `relq-codegen`  | `uv tool run relq-codegen`     | [Code generation](./codegen.md)                       |

`relq` itself depends only on `typing-extensions`. Each extra pins the executor
package to the same version as `relq`. `relq[all]` installs both executors.

`relq-migrate` has no dependencies for SQLite. Its PostgreSQL migrator needs the
`postgres` extra, and the `codegen` extra adds `relq-codegen` for the
`--codegen` option of the `relq-migrate` command:

```bash
uv add "relq-migrate[postgres]"
uv tool run "relq-migrate[codegen]" sqlite app.db migrations --codegen src/my_app/db_schema.py
```

`relq-codegen` is a development tool, so run it with `uv tool run` from the
application repository. Its SQLite path has no dependencies. Its PostgreSQL path
needs the `postgres` extra:

```bash
uv tool run relq-codegen sqlite path/to/app.db src/my_app/db_schema.py
uv tool run "relq-codegen[postgres]" postgres "$DATABASE_URL" src/my_app/db_schema.py
```

## Engine versions

relq compiles for two fixed dialects. Each has a minimum server version:

| Engine     | Minimum             | What sets it                                                                                                                                                                          |
| ---------- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SQLite     | 3.39.0 (2022-06-25) | `right_join` and `full_join` need `RIGHT` and `FULL OUTER JOIN`, added in 3.39.0. `RETURNING` and CTE `AS MATERIALIZED` need 3.35.0. Window frame `EXCLUDE` and `GROUPS` need 3.28.0. |
| PostgreSQL | 14 (2021-09-30)     | `date_bin` needs 14. CTE `AS MATERIALIZED` needs 12. Window frame `EXCLUDE` and `GROUPS` need 11. Everything else relq emits is older.                                                |

relq does not check the server version. The compiler produces SQL without a
connection, and an older server rejects the statement itself.
