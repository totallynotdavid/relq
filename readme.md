# relq

relq is a typed SQL query builder for Python 3.13+. It builds `SELECT`,
`INSERT`, `UPDATE`, and `DELETE` statements from declared tables and runs them
on SQLite (`sqlite3`) or PostgreSQL (`asyncpg`). Those are the only two engines.

Builders are immutable and values are sent as bound parameters. The one
exception is the constants in a PostgreSQL conflict arbiter predicate, which are
escaped and written into the SQL. The type checker or the compiler rejects an
invalid query before it reaches the database.

## Get started

```bash
uv add "relq[sqlite]"
# or
uv add "relq[postgres]"
```

```python
import sqlite3

from relq import Column, Table, column, select
from relq_sqlite import SQLiteDatabase


class Users(Table):
    id: Column[int] = column()
    email: Column[str] = column()
    active: Column[bool] = column()


users = Users("users")

connection = sqlite3.connect(":memory:")
connection.execute("create table users (id integer primary key, email text, active integer)")
connection.execute("insert into users values (1, 'ada@example.com', 1), (2, 'bob@example.com', 0)")

query = select(users.id, users.email).from_(users).where(users.active.is_true())
rows = SQLiteDatabase(connection).fetch_all(query)
print(rows)  # [(1, 'ada@example.com')]
```

`rows` has the static type `list[tuple[int, str]]`.

## Features

- Typed `SELECT`, `INSERT`, `UPDATE`, and `DELETE`, with aliases, self-joins,
  scalar and correlated subqueries, derived tables, CTEs, and set operations
  that behave the same on both engines.
- Upserts, aggregates, `GROUP BY` validation, and window functions.
- `Predicate` (`bool`) and `NullablePredicate` (`bool | None`), which keep SQL's
  `UNKNOWN` visible in the types.
- Raw driver tuples, or rows decoded into a dataclass or `NamedTuple` on
  request. Decoding never changes the SQL.
- Materialized CTEs on both engines. Schema-qualified tables and data-modifying
  CTEs on PostgreSQL.
- Transactions with savepoints, streaming on PostgreSQL, and a query observer
  for logging and timing.
- `relq-migrate`: forward-only SQL migrations for both engines, with a command
  that also regenerates the schema module.
- `relq-codegen`: generates the table declarations from an existing database.

Queries are built from typed expressions only. relq has no raw SQL fragments. A
PostgreSQL-only feature raises an error when compiled for SQLite. The
[dialects page](https://github.com/totallynotdavid/relq/blob/master/docs/dialects.md)
lists them.

## Documentation

The [manual](https://github.com/totallynotdavid/relq/blob/master/docs/readme.md)
covers installation, queries, execution, migrations, and code generation. Start
with
[Get started](https://github.com/totallynotdavid/relq/blob/master/docs/get-started.md).
The
[architecture](https://github.com/totallynotdavid/relq/blob/master/docs/architecture.md)
maps the code, and
[contributing](https://github.com/totallynotdavid/relq/blob/master/.github/contributing.md)
explains how to work on relq.

## License

relq is licensed under the
[Apache License 2.0](https://github.com/totallynotdavid/relq/blob/master/LICENSE).
