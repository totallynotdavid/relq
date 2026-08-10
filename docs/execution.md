# Execution

```python
import sqlite3

from relq_sqlite import SQLiteDatabase

database = SQLiteDatabase(sqlite3.connect("app.db"))
```

```python
import asyncpg

from relq_postgres import PostgresDatabase

pool = await asyncpg.create_pool(dsn)
database = PostgresDatabase(pool)
```

`SQLiteDatabase` wraps `sqlite3` and is synchronous throughout.
`PostgresDatabase` wraps `asyncpg` and is asynchronous throughout. It accepts an
existing `asyncpg.Pool` or `asyncpg.Connection`; creating and closing it is the
application's responsibility. asyncpg's prepared-statement cache doesn't survive
pgbouncer's `transaction` or `statement` pooling mode, so pass
`statement_cache_size=0` to `asyncpg.connect`/`create_pool` if your pool sits
behind it.

## Raw and mapped results are different states

`select(...)` and `.returning(...)` are raw-result queries: `fetch_all` /
`fetch_one` return the exact tuple type you selected, with the driver's own
values (SQLite's `0`/`1` for booleans, text timestamps, and so on).
`select_model(Model, ...)` and `.returning_model(Model, ...)` are mapped-result
queries: `fetch_all` / `fetch_one` return exactly the declared dataclass or
`NamedTuple`. There is no result-type union and no implicit driver-to-domain
conversion.

```python
@dataclass
class UserEmail:
    id: int
    email: str


rows = database.fetch_all_as(
    select(users.id, users.email).from_(users), row_adapter(UserEmail)
)
```

`fetch_all_as` / `fetch_one_as` are the explicit, one-off adapter path for a raw
query. `row_adapter(Model)` validates arity against the model before any row is
decoded.

## Transactions

```python
with database.transaction() as transaction:
    transaction.execute(insert_into(users).values(email="a@example.com"))
    rows = transaction.fetch_all(select(users.id, users.email).from_(users))
```

`PostgresDatabase.transaction()` is the same shape as an `async with` block,
matching its asynchronous methods.

## execute vs fetch

`execute` accepts only non-row-producing commands: plain
`INSERT`/`UPDATE`/`DELETE` without `RETURNING`. A `SELECT` or a `RETURNING`
query goes through `fetch_all`/`fetch_one`/`fetch_all_as`/`fetch_one_as`
instead. This is rejected statically where possible and at runtime otherwise, so
a returned row can never be silently discarded.

There is no generic `execute_many`. Homogeneous inserts use `values_many` (see
[Data manipulation](./dml.md)); heterogeneous work is explicit individual
`execute` calls inside a transaction.

SQL compilation is an internal executor concern. Application code executes
builders through a database adapter, never rendered SQL text directly. There is
no public query AST accessor, custom `Dialect`, or generic `compile_query`
surface: relq supports exactly SQLite and PostgreSQL.
