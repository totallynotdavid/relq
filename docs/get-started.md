# Get started

Declare a table, build a query, and run it:

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

`query` is immutable. Every builder method, such as `.from_`, `.where`, and
`.limit`, returns a new query. `fetch_all` returns the tuple shape you selected.
Here that is `list[tuple[int, str]]`, checked statically, not
`list[tuple[object, ...]]`.

If the database already exists, generate the table declarations instead of
writing them by hand. See [Code generation](./codegen.md).

[How relq works](./how-relq-works.md) describes the whole pipeline.
[Queries](./queries.md) and [Data manipulation](./dml.md) cover the builders.
