# How relq works

relq has four layers:

```text
handwritten declarations or database metadata
  -> optional relq-codegen, producing committed schema modules
  -> typed Table / DerivedTable / CteTable declarations and DML contracts
  -> immutable query and expression builders
  -> private tuple-backed AST
  -> semantic validation plus a fixed SQLite or PostgreSQL compiler
  -> sqlite3 or asyncpg executor
  -> raw driver tuples, or explicit RowAdapter decoding into a row model
```

1. **Schema.** A relation's shape is declared with `Table`, `DerivedTable`, or
   `CteTable`. It is never built at runtime from column-name strings. A table
   alias is a typed shallow copy of the same class, so `users.as_("manager").id`
   is still a `Column[int]`. See [Schema](./schema.md).
2. **Builders.** `select`, `insert_into`, `update`, and `delete_from` construct
   frozen AST nodes. `SelectQuery`, `InsertQuery`, `UpdateQuery`, and
   `DeleteQuery` are persistent values, so every method returns a new query.
   Clauses that would replace an earlier one (`from_`, `values`, `limit`,
   `returning`, and others) can be set only once.
3. **Compiler.** Validation and rendering run over the immutable AST. A query is
   validated once and rendered for exactly one dialect, SQLite or PostgreSQL.
   The AST and the renderer's state are private.
4. **Executor.** `relq-sqlite` and `relq-postgres` consume queries through a
   private execution boundary and never touch AST or builder internals.
   `fetch_all` and `fetch_one` accept `SELECT` and `RETURNING` queries. `execute`
   accepts only DML that returns no rows. An unadapted fetch returns the driver's
   own values, such as SQLite's `0` and `1` for booleans and text timestamps.
   `row_adapter(Model)` is the explicit conversion for `fetch_all_as` and
   `fetch_one_as`. It checks the row width first. `.decode(...)` embeds an
   adapter in the query. See [Execution](./execution.md).

The split makes three kinds of bug unrepresentable. Schema shape cannot be built
from user input at runtime. SQL structure comes only from closed builders with
parameterized values. A raw fetch is never mistaken for a decoded domain object.
[Design boundaries](./design-boundaries.md) lists everything this rules out.

Application query code imports from the top-level `relq` package. Its internal
module layout is described in [architecture.md](../architecture.md), for
contributors to relq. Applications that use migrations also import
`relq_migrate` directly.
