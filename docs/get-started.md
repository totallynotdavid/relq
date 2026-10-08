# Get started

This page builds a small program on SQLite. [Installation](./installation.md)
lists the packages for PostgreSQL.

## Declare a table

A table is a class. Each column's Python type is its annotation, and nullability
goes in the annotation:

```python
from relq import Column, Table, column


class Users(Table):
    id: Column[int] = column()
    email: Column[str] = column()
    active: Column[bool] = column()


users = Users("users")
```

If the database already exists, generate these declarations with
[relq-codegen](./codegen.md) instead of writing them by hand.

## Insert and query

```python
import sqlite3

from relq import insert_into, select
from relq_sqlite import SQLiteDatabase

connection = sqlite3.connect(":memory:")
connection.execute(
    "create table users (id integer primary key, email text not null, active integer not null)"
)
database = SQLiteDatabase(connection)

database.execute(insert_into(users).values(id=1, email="ada@example.com", active=True))
database.execute(insert_into(users).values(id=2, email="bob@example.com", active=False))

query = select(users.id, users.email).from_(users).where(users.active.is_true())
rows = database.fetch_all(query)  # list[tuple[int, str]]
print(rows)  # [(1, 'ada@example.com')]
```

`query` is immutable. Every builder method, such as `.from_`, `.where`, and
`.limit`, returns a new query. `fetch_all` returns the tuple shape you selected,
checked statically as `list[tuple[int, str]]`.

`execute` takes statements that return no rows. `fetch_all` and `fetch_one` take
`SELECT` and `RETURNING` queries. See [Execution](./execution.md).

## Decode rows into a model

A fetch returns the driver's own values. `.decode(Model)` returns a dataclass or
`NamedTuple` instead, without changing the SQL:

```python
from dataclasses import dataclass


@dataclass
class UserEmail:
    id: int
    email: str


emails = database.fetch_all(query.decode(UserEmail))
print(emails)  # [UserEmail(id=1, email='ada@example.com')]
```

## Next

- [Overview](./overview.md) shows the layers a query passes through.
- [Queries](./queries.md) and [Data manipulation](./dml.md) cover the builders.
- [Execution](./execution.md) covers PostgreSQL, transactions, and decoding.
