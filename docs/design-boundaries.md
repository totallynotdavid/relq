# Design boundaries

relq is deliberately narrow. Some of this is permanent: a boundary that makes a
category of bug unrepresentable. Some is just scope relq hasn't grown into yet.

## By design, not planned

- **Only SQLite and PostgreSQL.** There's no public custom dialect, generic
  compiler, or driver plugin API.
- **No raw SQL.** Normal builders accept structured expressions and
  parameterized values only: no raw SQL string, generic function-name builder,
  or arbitrary operator string. This is permanent, not a gap waiting to be
  filled: the closed expression surface is what makes relq's validation and
  cross-dialect claims mean anything. A capability relq doesn't model yet is
  added as a named, typed, validated expression (see
  [PostgreSQL-only extensions](#postgresql-only-extensions)), never as an
  escape hatch. If raw SQL is ever justified, it belongs in a separately named,
  explicitly-unsafe package with a visible loss of guarantees, not this one.
- **No dynamic schema construction.** `Table`, `DerivedTable`, and `CteTable`
  are declared classes. There's no `cte("name")`, `query.as_("alias")` as an
  untyped relation, or `.column("name")` accessor built from a runtime string.
- **No generic `execute_many`.** Homogeneous inserts use `values_many`, a
  dedicated bulk AST that emits one statement; heterogeneous work is explicit
  individual `execute` calls inside a transaction.
- **No implicit result decoding.** Raw fetches return exact driver tuples.
  Turning those into application values requires an explicit `RowAdapter`,
  either passed to `fetch_*_as` or embedded by `select_model` /
  `returning_model`. There is no result-type union.
- **No Python arithmetic operator overloading for SQL.** `add`, `subtract`,
  `multiply`, and `divide` are typed functions, not `+`/`-`/`*`/`/`. See
  [Expressions](./expressions.md).

## PostgreSQL-only extensions

Everything exported from the top-level `relq` package compiles for both
supported dialects. `relq.postgres` is the deliberate exception:

```python
from relq.postgres import cast_uuid, json_text, regex_match
```

These are PostgreSQL's `->>`, `~`/`~*`, and `::uuid`, as named typed functions
with declared result types and nullability. They obey the same rules as the
portable surface (closed grammar, parameterized operands, validated
structure) and differ only in reach: compiling a query that uses one for
SQLite is a compile-time error naming the expression, so a PostgreSQL-only
query can never quietly become a different SQLite query.

The separate import path is the point. A grep for `relq.postgres` finds every
query that has left the portable subset, which a keyword argument or a silently
dialect-dependent method would not.

This is an extension mechanism, not a loophole: there is no
`fn("some_function", ...)`, no operator string, and no caller-chosen cast
target. A PostgreSQL operator relq doesn't model yet needs a new named function
here and a new node in the compiler.

Two other capabilities are PostgreSQL-only for the same reason, and are rejected
at compile time for SQLite rather than ignored:

- **Schema-qualified tables** (`Table(name, schema=...)`). SQLite's qualified
  names address attached databases, which is a different thing. See
  [Schema](./schema.md).
- **Data-modifying CTEs** (`with_(source, delete_from(...).returning(...))`).
  SQLite has no such statement. See [Queries](./queries.md).

CTE materialization (`with_(..., materialized=True)`) is *not* in this group: it
compiles for both engines at relq's documented version floors: SQLite 3.35.0
(the release that also added `RETURNING`) and PostgreSQL 12. See
[Engine versions](./installation.md#engine-versions).

## Not yet

These are gaps, not commitments to never build them. They stay out until
something needs them:

- Generated-source formatting beyond deterministic `--check` output.
- Multi-schema generated modules. A generated table now carries the schema it
  was introspected from, but one `relq-codegen` run still covers one schema;
  spanning several in one module needs an explicit module/import and collision
  design first.
- Migrations, generic function builders, and custom dialects/plugins.
- Conflict predicates, named constraints, and PostgreSQL's `DO UPDATE ... WHERE`
  on `on_conflict`.
- Named windows.
