# Design boundaries

relq is narrow on purpose. Some of that is permanent, because a boundary makes a
category of bug unrepresentable. The rest is scope relq has not grown into yet.

## By design, not planned

- **Only SQLite and PostgreSQL.** There is no public custom dialect, generic
  compiler, or driver plugin API.
- **No raw SQL.** Builders accept structured expressions and parameterized
  values only. There is no raw SQL string, generic function-name builder, or
  arbitrary operator string. The closed expression surface is what gives relq's
  validation and cross-dialect guarantees their meaning, so this is not a gap
  waiting to be filled. A capability relq does not model yet is added as a named,
  typed, validated expression (see
  [PostgreSQL-only extensions](#postgresql-only-extensions)), never as an escape
  hatch. If raw SQL is ever justified, it belongs in a separate package that
  is named as unsafe and loses these guarantees visibly.
- **No dynamic schema construction.** `Table`, `DerivedTable`, and `CteTable`
  are declared classes. There is no untyped `cte("name")`, no untyped
  `query.as_("alias")` relation, and no `.column("name")` accessor built from a
  runtime string.
- **No generic `execute_many`.** Homogeneous inserts use `values_many`, which
  builds one statement. Heterogeneous work is individual `execute` calls inside
  a transaction.
- **No implicit result decoding.** Raw fetches return the driver's tuples.
  Turning them into application values takes an explicit `RowAdapter`, passed to
  `fetch_*_as` or embedded with `.decode(...)`. There is no result-type union.
- **No Python arithmetic operator overloading for SQL.** `add`, `subtract`,
  `multiply`, and `divide` are typed functions, not `+`, `-`, `*`, and `/`. See
  [Expressions](./expressions.md).

## PostgreSQL-only extensions

Most of what the top-level `relq` package exports compiles for both dialects.
Three groups are PostgreSQL-only and fail at compile time for SQLite:

- The `relq.postgres` module.
- The temporal builders exported from `relq` itself: `extract`, `date_trunc`,
  `date_bin`, `age`, the clock and `make_*` functions, interval arithmetic, and
  the rest of that family.
- The row-locking clauses `for_update()`, `for_share()`, and their variants.

Only `relq.postgres` sits behind a separate import:

```python
from relq.postgres import cast_uuid, json_text, regex_match
```

These are PostgreSQL's `->>`, `~` and `~*`, and `::uuid`, written as named typed
functions with declared result types and nullability. They follow the same rules
as the portable surface: a closed grammar, parameterized operands, and validated
structure. They differ only in reach. Compiling a query that uses one for SQLite
raises an error that names the expression, so a PostgreSQL query never turns
into a different SQLite query.

The separate import is what makes these extensions findable. Searching for
`relq.postgres` finds every query that uses one, which a keyword argument or a
method that silently depends on the dialect would not allow. The temporal
builders and locking clauses reject SQLite as well, but they are not behind that
import.

This is an extension mechanism, not a loophole. There is no
`fn("some_function", ...)`, no operator string, and no caller-chosen cast target.
A PostgreSQL operator relq does not model yet needs a new named function in
`relq.postgres` and a new node in the compiler.

Two more capabilities are PostgreSQL-only. SQLite rejects them at compile time
instead of ignoring them:

- **Schema-qualified tables** (`Table(name, schema=...)`). SQLite's qualified
  names address attached databases, which is a different thing. See
  [Schema](./schema.md).
- **Data-modifying CTEs** (`with_(source, delete_from(...).returning(...))`).
  SQLite has no such statement. See [Queries](./queries.md).

CTE materialization (`with_(..., materialized=True)`) is not in this group. It
compiles for both engines, because `AS MATERIALIZED` needs SQLite 3.35.0 or
PostgreSQL 12 and both are below relq's version floors. See
[Engine versions](./installation.md#engine-versions).

## Not yet

These are gaps, not commitments to never build them. They stay out until
something needs them:

- Generated-source formatting beyond deterministic `--check` output.
- Multi-schema generated modules. A generated table carries the schema it was
  introspected from, but one `relq-codegen` run still covers one schema.
  Spanning several in one module needs a module, import, and collision design
  first.
- Generic function builders and custom dialects or plugins.
- Named constraints as an `on_conflict` target, and conflict predicates on
  SQLite. PostgreSQL has both. See [Data manipulation](./dml.md).
- Named windows.
