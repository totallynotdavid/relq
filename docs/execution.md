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
behind it. The PostgreSQL executor also relies on asyncpg 0.31's private
transaction bookkeeping to recover failed controlled transactions. The package
therefore pins asyncpg to `>=0.31,<0.32`; upgrade beyond that range only after
the private transaction attributes used by the executor have been revalidated.

## Raw and decoded results

`select(...)` and `.returning(...)` are raw-result queries: `fetch_all` /
`fetch_one` return the exact tuple type you selected, with the driver's own
values (SQLite's `0`/`1` for booleans, text timestamps, and so on).
`.decode(Model)` attaches a declared decoder to the same SELECT or DML builder.
It preserves the query's SQL projection and every valid composition operation,
but `fetch_all` / `fetch_one` return the declared dataclass or `NamedTuple`.
Decoding never changes what a query can mean in SQL.

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

`fetch_all_as` / `fetch_one_as` are the explicit, one-off adapter path for a raw
query. Embedded decoders are for reusable query contracts; executor-time decoders
are for one call. `row_adapter(Model)` validates arity before any row is decoded.

`fetch_one_or_raise` and `fetch_one_as_or_raise` are the required-row variants.
They raise `NoResultError` when the query returns no row, or raise the exception
returned by the optional zero-argument `error` factory instead. The original
`fetch_one` and `fetch_one_as` methods remain optional-result operations.

## Transactions

```python
with database.transaction() as transaction:
    transaction.execute(insert_into(users).values(email="a@example.com"))
    rows = transaction.fetch_all(select(users.id, users.email).from_(users))
```

`PostgresDatabase.transaction()` is the same shape as an `async with` block,
matching its asynchronous methods. The block yields the database interface so
it can be passed to code that accepts `PostgresDatabase` (or `SQLiteDatabase`),
while the runtime handle also enforces controlled-transaction lifecycle rules.

For work that must coordinate an external operation with an open transaction,
use the controlled handle. SQLite is synchronous; PostgreSQL's corresponding
methods are asynchronous.

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
after its owning transaction completes. The context-manager `transaction()`
method remains the convenient automatic commit/rollback wrapper. Both adapters
export `ControlledTransaction` and the shared `TransactionUnavailableError` for
typing and lifecycle-error handling. PostgreSQL also exports `Connection`, the
type yielded by `transaction_connection()`.

If PostgreSQL must recover a failed transaction, every relq-controlled handle
sharing that physical connection is invalidated before the raw connection is
reset. A wrapper created around the connection for coordinated work must
therefore treat `TransactionUnavailableError` as terminal for that transaction.

SQLite has one real transaction per connection. `begin()` starts a real
transaction using the connection's configured `isolation_level`; if an
implicit transaction is already open, the handle adopts it and its `commit()`
or `rollback()` completes that transaction. A further `begin()` while a
controlled transaction is active creates a uniquely named savepoint. Nested
controlled handles are dependent: completing a parent invalidates every
still-open child handle, and using a child afterward raises immediately. A
parent commit also commits writes already made through those child handles
because they belong to the same underlying transaction; a parent rollback
discards them. The child handle is invalidated, not its already-applied work.
Use separate SQLite connections for genuinely concurrent independent
transactions.

Savepoint names must be unique among active savepoints on a transaction
connection. Releasing a savepoint makes its name available for reuse. Rolling
back a savepoint also invalidates every later savepoint handle, because the
database destroys those descendant savepoints at the same time.
PostgreSQL savepoint names are limited to 63 UTF-8 bytes. SQLite quoted
savepoint names do not have PostgreSQL's identifier-length limit.

When application code needs the physical PostgreSQL connection—for example to
enqueue work in the same transaction—make that boundary explicit:

```python
# Pass the same observer callback used to construct `database`.
async with database.transaction_connection() as connection:
    await PostgresDatabase(connection, observer=observer).execute(query)
    await queue_work(connection)
```

The context commits on normal exit and rolls back on an exception. With a
database backed by a pool, the acquired connection stays reserved for the
whole context.

## Query events

Both executors accept an optional observer. It receives one immutable
`QueryEvent` for every driver operation: `fetch`, `execute`, transaction-control
SQL, and PostgreSQL `fetch_iter` streams.
The event contains the compiled SQL, the positional parameter tuple, elapsed
duration in seconds, the returned/affected row count, and an exception for a
failed operation. Failed events have `row_count=None`, and the original driver
exception is re-raised after the event is emitted. Observer callback failures
are logged and ignored so instrumentation cannot replace a successful result
or obscure the original driver exception. Observer-raised ordinary
`Exception`s are handled this way; process-control exceptions such as
`KeyboardInterrupt` and `SystemExit` are allowed to propagate.
For `fetch_iter`, an exception raised by the consumer is not attributed to the
query: it is re-raised with the count of rows successfully streamed. Cursor,
row-mapping, and transaction setup/teardown failures are the errors recorded
on the event.

```python
from relq_sqlite import QueryEvent, SQLiteDatabase


def observe(event: QueryEvent) -> None:
    print(event.sql, event.duration, event.error)


database = SQLiteDatabase(connection, observer=observe)
```

This observer is the deliberate boundary where compiled output becomes
visible; relq does not expose its AST or compiler as a public query-rewriting
API. Parameters are passed to the driver unchanged. In particular, relq does
not own a backend-aware JSON/JSONB encoder: PostgreSQL callers should pass
asyncpg/psycopg-compatible JSON wrappers when their driver requires one, while
SQLite callers should pass values supported by SQLite's adapter. This keeps
driver-specific adaptation explicit and preserves the current parameterized
value contract.

If SQLite `begin()` adopts an implicit transaction that is already open, it
does not emit a synthetic `BEGIN` event because no `BEGIN` statement ran; the
event stream starts with the work performed inside the adopted transaction and
its eventual `COMMIT` or `ROLLBACK`.

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
surface: relq supports exactly SQLite and PostgreSQL. The observer is the
documented execution-time inspection seam.
