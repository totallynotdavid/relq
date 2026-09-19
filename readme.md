# relq

relq is a type-safe SQL query builder for Python 3.13+. It supports SQLite and
PostgreSQL only. Builders are immutable, expressions are structured, and values
are always parameterized, so a query that would be invalid or unsafe fails to
type-check or compile instead of failing at runtime.

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

- Typed `SELECT`, `INSERT`, `UPDATE`, and `DELETE`, with aliases, self-joins,
  predicates, scalar and correlated subqueries, declared derived relations and
  CTEs, and set operations that behave the same on both engines.
- Upserts, aggregates, `GROUP BY` validation, and window functions on both
  engines.
- `Predicate` (`bool`) and `NullablePredicate` (`bool | None`), which keep SQL's
  `UNKNOWN` visible in the types.
- Raw driver tuples, or explicitly decoded model results. Decoding never affects
  the SQL.
- Schema-qualified tables, materialized CTEs, and data-modifying CTEs, such as
  `WITH removed AS (DELETE ... RETURNING id) SELECT count(*) FROM removed`.
- `relq-codegen`, which generates a schema module from an existing database.

relq has no raw SQL, generic function builder, custom dialect, or implicit
result decoding, on purpose. PostgreSQL-only capabilities are named, typed,
validated expressions, and `relq.postgres` holds the ones that need a separate
import. Compiling one for SQLite raises an error. See
[Design boundaries](./docs/design-boundaries.md) for the full list and the
reasons.

## Documentation

The [manual](./docs/readme.md) covers installation, queries and DML, execution,
migrations, and code generation. Start with
[Get started](./docs/get-started.md).

## Contributing

See [contributing.md](./contributing.md).
