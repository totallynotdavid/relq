# Data manipulation

`insert_into`, `update`, and `delete_from` are separate builder families, one
per statement.

## Insert

```python
insert_into(users).values(id=1, email="ada@example.com")
```

```python
insert_into(users).values_many(
    ({"id": 1, "email": "ada@example.com"}, {"id": 2, "email": "grace@example.com"})
)
```

`values_many` materializes its input once, validates every row has the same
ordered column shape, and compiles to a single `INSERT ... VALUES (...), (...)`
statement: one round trip instead of one statement per row, with coherent
`RETURNING` semantics. SQLite's 999-parameter ceiling and PostgreSQL's
65,535-parameter ceiling are both checked at compile time, so callers chunk
intentionally instead of hitting an opaque driver failure.

```python
copied = insert_into(user_archive).from_select(
    select(users.id, users.email).from_(users),
    user_archive.id,
    user_archive.email,
)
```

`from_select` takes target `Column` objects and validates the projection width
against them.

## Conflict-aware insert (upsert)

```python
upsert = (
    insert_into(users)
    .values(id=42, email="ada@example.com", name="Ada")
    .on_conflict(users.email)
    .do_update(name=excluded(users.name))
    .returning(users.id)
)
```

`on_conflict(...)` is shared by SQLite and PostgreSQL and must be completed with
`.do_nothing()` or `.do_update(...)`; `excluded(column)` refers to the proposed
row. Conflict predicates, named constraints, and PostgreSQL's
`DO UPDATE ... WHERE` aren't part of this cross-dialect surface yet. See
[Design boundaries](./design-boundaries.md).

## Update and delete

```python
update(users).values(login_count=add(users.login_count, 1)).where(users.id.eq(42))

delete_from(users).where(users.id.eq(42))
```

Neither compiles until `.where(...)` bounds it or `.all_rows()` records an
intentional full-table operation. An accidental unbounded `UPDATE`/`DELETE` is a
build-time error, not a production incident.

## Returning

```python
insert_into(users).values(email="ada@example.com").returning(users.id, users.email)
```

`.returning(...)` turns a DML builder into a row-producing query, so it goes to
`fetch_all`/`fetch_one`, not `execute`. Tuple `returning` overloads stop at eight
expressions, matching `select`; wider results use
`returning_model(Model, ...)` / `select_model(Model, ...)`, which fix arity and
attach executor-only decoding rather than degrading to `tuple[object, ...]`.
