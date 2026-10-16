relq 0.1.0 is out.

It comes nine weeks after 0.0.1 and breaks the 0.0.1 API, so read the breaking
changes before you upgrade. Code that only uses `select`, `insert_into`,
`update`, `delete_from` and the executors needs one edit: every `column(int)`
becomes `column()`. If you generated your schema with `relq-codegen`, regenerate
it.

The biggest change is that a column's type now comes from its annotation, and
`Table[T]` carries the row type. That removed the `Model*Query` classes and
`select_model`. A dataclass or `NamedTuple` result is now one method on any
query: `.decode(Model)`. A query can also return a whole relation with
`select_all_from` and `returning_all_from`. `returning` still takes at most
eight expressions. `returning_all_from` has no limit.

The second change is transactions and observability. Both executors now have
`begin()`, savepoints and a query observer that receives the SQL, parameters,
duration and row count of every driver call. `fetch_one_or_raise` raises
`NoResultError` when there is no row.

PostgreSQL gained the most. There are guarded upserts with an arbiter predicate
and an action predicate, row locking (`for_update().skip_locked()` and the other
three clauses), data-modifying CTEs through `with_modifying`, and a temporal
vocabulary: `extract`, `date_trunc`, `age`, interval arithmetic and the `make_*`
constructors. Both executor packages gained `row_batches`, which splits a large
`values_many` under the driver's limit: 999 parameters on SQLite, 32,767 on
PostgreSQL.

There is also a new distribution, `relq-migrate`, which has never been on PyPI
before. It applies numbered SQL files to SQLite or PostgreSQL and, with
`--codegen`, regenerates the schema module in the same run. `relq-codegen`
gained `--config`, `--exclude-table` and one-line error messages. relq now
supports Python 3.13 and later, and the repository has an Apache-2.0 licence.
The rest is tests and a rewrite of the manual.

## Breaking changes

`column()` takes no type argument. The annotation supplies it, which also lets a
recursive alias such as `JsonValue` work.

```python
from relq import Column, Table, column


class Users(Table):
    id: Column[int] = column(int)
    nickname: Column[str | None] = column(str)
```

```python
from relq import Column, Table, column


class Users(Table):
    id: Column[int] = column()
    nickname: Column[str | None] = column()
```

`select_model` and `returning_model` are gone. Call `.decode(Model)` on a
`select` or on a `returning` query. The SQL is the same.

```python
from dataclasses import dataclass
from relq import Column, Table, column, insert_into, select_model


class Users(Table):
    id: Column[int] = column(int)
    email: Column[str] = column(str)


users = Users("users")


@dataclass
class UserEmail:
    id: int
    email: str


list_users = select_model(UserEmail, users.id, users.email).from_(users)
add_user = (
    insert_into(users)
    .values(id=2, email="b@example.com")
    .returning_model(UserEmail, users.id, users.email)
)
```

```python
from dataclasses import dataclass
from relq import Column, Table, column, insert_into, select


class Users(Table):
    id: Column[int] = column()
    email: Column[str] = column()


users = Users("users")


@dataclass
class UserEmail:
    id: int
    email: str


list_users = select(users.id, users.email).from_(users).decode(UserEmail)
add_user = (
    insert_into(users)
    .values(id=2, email="b@example.com")
    .returning(users.id, users.email)
    .decode(UserEmail)
)
```

`ModelSelectQuery`, `ModelInsertQuery`, `ModelUpdateQuery` and
`ModelDeleteQuery` are gone. `insert_into`, `update` and `delete_from` take the
table directly and return `InsertQuery`, `UpdateQuery` and `DeleteQuery`. Change
the names in your annotations.

```python
from relq import ModelSelectQuery, ModelInsertQuery
```

```python
from relq import SelectQuery, InsertQuery
```

Generated schema modules from 0.0.1 do not import, because they call
`column(int)`. Regenerate them. `relq-codegen --check` reports the old file as
stale, which is the signal to do it. The generated row dataclass is now frozen
without `__slots__`, and the generated `Table` subclass names its row type. Two
more changes in `relq-codegen`: a SQLite database file that does not exist is
now an error where it used to be created empty, and `relq_codegen.render` needs
a `dialect="sqlite"` or `dialect="postgres"` argument.

```bash
relq-codegen sqlite app.db src/my_app/db_schema.py
```

On PostgreSQL, `fetch_iter` streams a `SELECT` with a server-side cursor and
raises `TypeError` for `INSERT`, `UPDATE` or `DELETE` with `RETURNING`, and for
queries with write CTEs. PostgreSQL cannot declare a cursor for those. 0.0.1 let
such a call run. Use `fetch_all`.

```python
async with database.fetch_iter(insert_into(t).values(a=1).returning(t.a)) as rows:
    async for row in rows:
        ...
```

```python
rows = await database.fetch_all(insert_into(t).values(a=1).returning(t.a))
```

Several names that were reachable but never documented are gone:
`Column.python_type`, `Order.from_expression`, the `.node()` method on
expressions and tables, and `relq_codegen.mapping.relq_decoder` and `Union`. The
manual never described them.

## Fixes

- After an outer join, `x.nullable().as_("a")` and `add(x.nullable(), 1)` were
  rejected with "outer join can NULL-extend selected expression" while
  `x.as_("a").nullable()` passed. All of them pass now.
- `values_many` past PostgreSQL's 32,767 parameters failed in the driver with an
  `InterfaceError`. It now raises `ValueError` while compiling. The 0.0.1 manual
  said 65,535, which was wrong.
- `relq-codegen postgres` with a malformed URL printed a traceback. It now
  prints one `relq-codegen: ...` line and exits non-zero.

## Known problems

None known.

## What to do

Upgrade the distributions together. The extras and the executors pin exact
versions: `relq[sqlite]` installs `relq-sqlite==0.1.0`, `relq[postgres]`
installs `relq-postgres==0.1.0`, both executors require `relq==0.1.0`, and
`relq-migrate[codegen]` installs `relq-codegen==0.1.0`:

```bash
uv add "relq[sqlite]==0.1.0"
# or
uv add "relq[postgres]==0.1.0"
```

Replace `column(int)` with `column()`, replace `select_model` and
`returning_model` with `.decode`, regenerate your schema modules, then run your
type checker. Most breakage shows up there. Report problems at
https://github.com/totallynotdavid/relq/issues.
