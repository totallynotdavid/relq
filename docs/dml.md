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
row. Named constraints as a conflict target aren't part of this surface yet. See
[Design boundaries](./design-boundaries.md).

A conflict target takes **any number of columns**, and they need not share a
value type: a composite target such as `(queue, dedupe_key)` mixes `Column[str]`
with `Column[str | None]` and still type-checks. This is the one place the
builder deliberately drops the per-position type parameters that `from_select`
uses. `from_select` needs them, because each target column is checked against
the expression the projection supplies for it; a conflict target is never
related to a projection or to the returned rows, only read for its identity, so
its value type is widened away on the way in, through `ConflictTarget`, the
covariant base `Column` inherits. That leaves no arity ceiling to document, and
no need to spell every supported width as an overload. `ConflictTarget` is a
nominal base inside the `Expression` family rather than a protocol, so
`on_conflict` still accepts only real columns: a lookalike that doesn't inherit
it is a type error, and the base and its subclasses carry relq's usual
construction seal, so they can't be built outside relq to be passed.

### Conflict predicates (PostgreSQL only)

Both of SQL's `ON CONFLICT` predicates are spelled `.where(...)`, in the same
positions the SQL clauses occupy:

```python
(
    insert_into(jobs)
    .values(queue="emails", dedupe_key="welcome:7", state="pending")
    .on_conflict(jobs.queue, jobs.dedupe_key)
    .where(jobs.dedupe_key.is_not_null() & jobs.state.in_(("pending", "leased")))
    .do_update(updated_at=jobs.updated_at)
    .where(jobs.state.eq("pending"))
)
```

The first `.where(...)` is the *arbiter* predicate: it repeats a partial unique
index's own predicate so PostgreSQL can infer that index, and it is required
whenever the unique index is partial. The second is the *action* predicate: the
conflicting row is updated only where it holds, which makes `do_update` a
compare-and-swap rather than an unconditional overwrite.

`.do_update(...)` returns a `ConflictUpdateQuery`, which is already executable;
its `.where(...)` must come before `.returning(...)`, matching SQL's own order.
`excluded(column)` is allowed in the action predicate and rejected in the
arbiter predicate, which describes stored rows rather than the proposed one.
Compiling either predicate for SQLite raises: relq only emits them for
PostgreSQL.

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
`.returning(...).decode(Model)` / `select(...).decode(Model)`. For a declared
relation wider than eight columns, use `select_all_from(relation)` before
decoding; its declared row shape remains exact.
