# Design boundaries

relq is deliberately narrow. Some of this is permanent: a boundary that makes a
category of bug unrepresentable. Some is just scope relq hasn't grown into yet.

## By design, not planned

- **Only SQLite and PostgreSQL.** There's no public custom dialect, generic
  compiler, or driver plugin API.
- **No raw SQL.** Normal builders accept structured expressions and
  parameterized values only: no raw SQL string, generic function-name builder,
  or broad escape hatch. If raw SQL is ever justified, it belongs in a
  separately named, explicitly-unsafe package with a visible loss of guarantees,
  not the normal one.
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

## Not yet

These are gaps, not commitments to never build them. They stay out until
something needs them:

- Generated-source formatting beyond deterministic `--check` output.
- Multi-schema generated modules: needs an explicit module/import and collision
  design first.
- Migrations, raw SQL, generic function builders, and custom dialects/plugins.
- Conflict predicates, named constraints, and PostgreSQL's `DO UPDATE ... WHERE`
  on `on_conflict`.
- Named windows.
