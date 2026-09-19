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

`SQLiteDatabase` wraps `sqlite3` and is synchronous. `PostgresDatabase` wraps
`asyncpg` and is asynchronous. It accepts an existing `asyncpg.Pool` or
`asyncpg.Connection`, and the application creates and closes it.

asyncpg's prepared-statement cache does not survive pgbouncer's `transaction` or
`statement` pooling mode. Behind pgbouncer, pass `statement_cache_size=0` to
`asyncpg.connect` or `create_pool`.

To recover a failed controlled transaction, the PostgreSQL executor uses
asyncpg 0.31's private transaction attributes. `relq-postgres` therefore pins
asyncpg to `>=0.31,<0.32`. Before widening that range, check that those
attributes still behave the same.

## Raw and decoded results

`select(...)` and `.returning(...)` build raw-result queries. `fetch_all` and
`fetch_one` return the tuple type you selected, holding the driver's own values,
such as SQLite's `0` and `1` for booleans and text timestamps. `.decode(Model)`
attaches a declared decoder to the same SELECT or DML builder. The SQL
projection and every composition operation stay as they were, but `fetch_all`
and `fetch_one` now return the declared dataclass or `NamedTuple`. Decoding never
changes the SQL.

```python
@dataclass
class UserEmail:
    id: int
    email: str


decoded = select(users.id, users.email).from_(users).decode(UserEmail)


rows = database.fetch_all_as(
    select(users.id, users.email).from_(users),
    row_adapter(UserEmail),
)
```

`fetch_all_as` and `fetch_one_as` apply an adapter to a raw query for one call.
Use `.decode(...)` for a reusable query contract and `fetch_*_as` for a single
call. `row_adapter(Model)` checks the row width before decoding any row.

`fetch_one_or_raise` and `fetch_one_as_or_raise` require a row. When the query
returns none, they raise `NoResultError`, or the exception returned by the
optional zero-argument `error` factory. `fetch_one` and `fetch_one_as` return
`None` instead.

## Transactions

```python
with database.transaction() as transaction:
    transaction.execute(insert_into(users).values(email="a@example.com"))
    rows = transaction.fetch_all(select(users.id, users.email).from_(users))
```

`PostgresDatabase.transaction()` is used as `async with`, matching its
asynchronous methods. The block yields the database interface, so the handle can
be passed to code that accepts `PostgresDatabase` or `SQLiteDatabase`. At runtime
the handle also enforces the controlled-transaction lifecycle rules.

To coordinate an external operation with an open transaction, use the controlled
handle. Its SQLite methods are synchronous and its PostgreSQL methods are
asynchronous.

```python
transaction = database.begin()
transaction.execute(insert_into(users).values(email="a@example.com"))
savepoint = transaction.savepoint("after_user")
savepoint.rollback()
savepoint.release()
transaction.commit()
```

```python
transaction = await database.begin()
await transaction.execute(insert_into(users).values(email="a@example.com"))
savepoint = await transaction.savepoint("after_user")
await savepoint.rollback()
await savepoint.release()
await transaction.commit()
```

A controlled handle is unusable after `commit()` or `rollback()`. Its
`savepoint()` handle supports `rollback()` and `release()` and becomes unusable
once its owning transaction completes. The `transaction()` context manager
remains the simple way to commit or roll back automatically. Both adapters export
`ControlledTransaction` and the shared `TransactionUnavailableError`.
PostgreSQL also exports `Connection`, the type that `transaction_connection()`
yields.

To recover a failed transaction, PostgreSQL first invalidates every
relq-controlled handle on that physical connection and then resets the raw
connection. A wrapper you create around the connection must treat
`TransactionUnavailableError` as terminal for that transaction.

SQLite has one real transaction per connection. `begin()` starts a transaction
using the connection's `isolation_level`. If an implicit transaction is already
open, the handle adopts it, and its `commit()` or `rollback()` completes it. A
further `begin()` while a controlled transaction is active creates a uniquely
named savepoint.

Nested controlled handles depend on their parent. Completing a parent
invalidates every child handle that is still open, and using one afterward
raises immediately. Writes already made through a child belong to the same
underlying transaction, so a parent commit keeps them and a parent rollback
discards them. Only the child handle is invalidated, not its applied work. Use
separate SQLite connections for independent concurrent transactions.

Savepoint names must be unique among the active savepoints on a connection.
Releasing a savepoint frees its name for reuse. Rolling back a savepoint also
invalidates every later savepoint handle, because the database destroys those
savepoints too. PostgreSQL limits savepoint names to 63 UTF-8 bytes. SQLite has
no such limit on quoted savepoint names.

To use the physical PostgreSQL connection directly, for example to enqueue work
in the same transaction, ask for it explicitly:

```python
# Pass the same observer callback used to construct `database`.
async with database.transaction_connection() as connection:
    await PostgresDatabase(connection, observer=observer).execute(query)
    await queue_work(connection)
```

The context commits on normal exit and rolls back on an exception. With a
pool-backed database, the acquired connection stays reserved for the whole
context.

## Query events

Both executors accept an optional observer. It receives one immutable
`QueryEvent` for every driver operation: `fetch`, `execute`, transaction-control
SQL, and PostgreSQL `fetch_iter` streams. The event holds the compiled SQL, the
positional parameter tuple, the elapsed time in seconds, the returned or
affected row count, and the exception for a failed operation. A failed event has
`row_count=None`, and the driver exception is re-raised after the event is
emitted.

An observer that raises an ordinary `Exception` is logged and ignored, so
instrumentation cannot replace a successful result or hide the driver's
exception. `KeyboardInterrupt`, `SystemExit`, and other process-control
exceptions propagate.

For `fetch_iter`, an exception raised by the consumer is not attributed to the
query. It is re-raised, and the event carries no error and the count of rows
streamed so far. Cursor, row-mapping, and transaction setup or teardown failures
are recorded as the event's error.

```python
from relq_sqlite import QueryEvent, SQLiteDatabase


def observe(event: QueryEvent) -> None:
    print(event.sql, event.duration, event.error)


database = SQLiteDatabase(connection, observer=observe)
```

The observer is the only place where compiled output becomes visible. relq does
not expose its AST or compiler as a query-rewriting API.

Parameters reach the driver unchanged. relq has no backend-aware JSON or JSONB
encoder. On PostgreSQL, pass the JSON wrapper your driver requires. On SQLite,
pass values that SQLite's adapter supports.

When SQLite `begin()` adopts an implicit transaction that is already open, no
`BEGIN` runs, so no `BEGIN` event is emitted. The event stream starts with the
work done inside the adopted transaction and ends with its `COMMIT` or
`ROLLBACK`.

## execute vs fetch

`execute` accepts only commands that produce no rows: `INSERT`, `UPDATE`, and
`DELETE` without `RETURNING`. A `SELECT` or a `RETURNING` query goes through
`fetch_all`, `fetch_one`, `fetch_all_as`, or `fetch_one_as`. The type checker
rejects the wrong choice where it can and the executor rejects it at runtime
otherwise, so a returned row is never discarded silently.

There is no generic `execute_many`. Homogeneous inserts use `values_many` (see
[Data manipulation](./dml.md)). Heterogeneous work is individual `execute` calls
inside a transaction.

Compilation happens inside the executor. Application code runs builders through
a database adapter and never handles rendered SQL. There is no public AST
accessor, custom `Dialect`, or `compile_query` function.
