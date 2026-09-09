# Get started

Declare your tables, build a query, and run it:

```python
import sqlite3

from relq import Column, Table, column, select
from relq_sqlite import SQLiteDatabase


class Users(Table):
    id: Column[int] = column(int)
    email: Column[str] = column(str)
    active: Column[bool] = column(bool)


users = Users("users")

query = select(users.id, users.email).from_(users).where(users.active.is_true())

database = SQLiteDatabase(sqlite3.connect("app.db"))
rows = database.fetch_all(query)  # list[tuple[int, str]]
```

`query` is an immutable value: every builder method (`.from_`, `.where`,
`.limit`, ...) returns a new query rather than mutating the one it was called
on. `fetch_all` returns exactly the tuple shape you selected: here,
`list[tuple[int, str]]`, checked statically, not `list[tuple[object, ...]]`.

If your database already exists, generate the table declarations instead of
writing them by hand: see [Code generation](./codegen.md).

Continue to [How relq works](./how-relq-works.md) for the full pipeline, or jump
straight to [Queries](./queries.md) and [Data manipulation](./dml.md).
