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

`values_many` reads its input once, requires every row to have the same columns
in the same order, and compiles to one `INSERT ... VALUES (...), (...)`
statement. The compiler checks SQLite's 999-parameter limit and PostgreSQL's
65,535-parameter limit, so a batch that is too large fails with a relq error
before it reaches the driver. Split large batches yourself.

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

`on_conflict(...)` works on both SQLite and PostgreSQL. It must be completed with
`.do_nothing()` or `.do_update(...)`. `excluded(column)` refers to the proposed
row. Named constraints are not supported as a conflict target yet. See
[Design boundaries](./design-boundaries.md).

The target takes any number of columns, because a composite unique index does.
Its columns are typed as `ConflictTarget`, the unparameterized base that every
`Column` inherits, so one target can mix value types and nullability. A
`(queue, dedupe_key)` target with a nullable `dedupe_key` is an example.
`from_select(...)` and `returning(...)` limit their arity because each position
feeds the result tuple. A conflict target does not, so it has no limit.

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

The first `.where(...)` is the arbiter predicate. It repeats a partial unique
index's own predicate so that PostgreSQL can infer the index, and it is required
whenever that index is partial. The second is the action predicate. The
conflicting row is updated only where it holds, which makes `do_update` a
compare-and-swap instead of an unconditional overwrite.

The arbiter predicate compiles with inlined constants, not parameters, because a
generic plan would otherwise stop matching the index. It accepts only columns,
text, integer, boolean, and `NULL` constants, comparisons, `IN`, `BETWEEN`, `IS`
checks, `AND`, and `OR`.

`.do_update(...)` returns a `ConflictUpdateQuery`, which is already executable.
Its `.where(...)` must come before `.returning(...)`, matching SQL's own order.
`excluded(column)` is allowed in the action predicate and rejected in the
arbiter predicate, which describes stored rows instead of the proposed one.
Compiling either predicate for SQLite raises an error.

## Update and delete

```python
update(users).values(login_count=add(users.login_count, 1)).where(users.id.eq(42))

delete_from(users).where(users.id.eq(42))
```

Neither compiles until `.where(...)` bounds it or `.all_rows()` marks a
deliberate full-table operation. An unbounded `UPDATE` or `DELETE` is a build
error.

## Returning

```python
insert_into(users).values(email="ada@example.com").returning(users.id, users.email)
```

`.returning(...)` turns a DML builder into a row-producing query, so it goes to
`fetch_all` or `fetch_one`, not `execute`. It takes at most eight expressions,
like `select`. `.decode(Model)` does not raise that limit, because it attaches a
decoder to a projection that already exists. For a declared relation with more
than eight columns, use `select_all_from(relation)` before decoding. That is the
only way past the limit, and it covers a whole relation only. `returning` has no
wider form.
