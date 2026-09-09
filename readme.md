# relq

relq is a type-safe SQL query builder for Python 3.15+. It targets SQLite and
PostgreSQL only, and makes invalid or dangerous query construction hard to
express before runtime: immutable builders, structured expressions,
parameterized values only.

```bash
uv add "relq[sqlite]"
# or
uv add "relq[postgres]"
```

```python
from relq import Column, Table, column, select


class Users(Table):
    id: Column[int] = column(int)
    email: Column[str] = column(str)
    active: Column[bool] = column(bool)


users = Users("users")
query = select(users.id, users.email).from_(users).where(users.active.is_true())
```

## Features

- Typed `SELECT`/`INSERT`/`UPDATE`/`DELETE`, aliases and self-joins, predicates,
  scalar/correlated subqueries, declared derived relations and CTEs, portable
  set operations.
- Conflict-aware upserts, aggregates, portable `GROUP BY` validation, and window
  functions on both engines.
- SQL truth kept honest: `Predicate` (`bool`) vs `NullablePredicate`
  (`bool | None`), matching SQL's `UNKNOWN`.
- Raw driver results and typed model results are distinct static states, with no
  implicit decoding.
- Schema-qualified tables, materialized CTEs, and data-modifying CTEs
  (`WITH removed AS (DELETE ... RETURNING id) SELECT count(*) FROM removed`).
- `relq-codegen` generates committed schema modules from an existing database.

There is intentionally no raw SQL, generic function builder, custom dialect, or
implicit result decoding. PostgreSQL-only capabilities live behind a separate
`relq.postgres` import as named, typed, validated expressions, and compiling one
for SQLite is a compile-time error rather than a silent difference. See
[Design boundaries](./docs/design-boundaries.md) for the complete list and the
reasoning.

## Documentation

The [manual](./docs/readme.md) covers installation, the full query and DML
surface, execution, and code generation. Start at
[Get started](./docs/get-started.md).

## Contributing

See [contributing.md](./contributing.md).
