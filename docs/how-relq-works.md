# How relq works

relq has four layers, and each is a separate package concern:

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
   `CteTable`, never built at runtime from column-name strings. A table alias is
   a typed, shallow copy of the same class, so `users.as_("manager").id` stays
   statically `Column[int]`. See [Schema](./schema.md).
2. **Builders.** `select`, `insert_into`, `update`, and `delete_from` construct
   frozen AST nodes. `SelectQuery`, `InsertQuery`, `UpdateQuery`, and
   `DeleteQuery` are persistent values: every method returns a new query, and
   clauses that would otherwise silently replace meaning (`from_`, `values`,
   `limit`, `returning`, ...) are single-assignment.
3. **Compiler.** Validation and rendering run over the immutable AST. A query is
   checked once and rendered for exactly one dialect, SQLite or PostgreSQL.
   Nothing about the AST or the renderer's internal state is public.
4. **Executor.** `relq-sqlite` and `relq-postgres` consume queries through a
   private execution boundary, not AST or builder internals. `fetch_all` /
   `fetch_one` accept `SELECT` and `RETURNING` queries; `execute` accepts only
   non-row-producing DML. An unadapted fetch returns the driver's own tuple
   values (SQLite's `0`/`1` for booleans, text timestamps, and so on) instead of
   pretending they're already Python domain objects. `row_adapter(Model)` is the
   explicit, arity-checked conversion boundary for `fetch_all_as` /
   `fetch_one_as`; `.decode(...)` embeds that adapter directly into a query.
   See [Execution](./execution.md).

This split exists to make a category of bug unrepresentable rather than just
discouraged: schema shape can't be assembled from user input at runtime, SQL
structure is only ever composed from closed builders with parameterized values,
and a raw fetch can never be silently mistaken for a decoded domain object. See
[Design boundaries](./design-boundaries.md) for the complete list of what this
rules out.

Application code should only ever import from the top-level `relq` package; its
internal module layout is described in [architecture.md](../architecture.md) for
contributors working on relq itself.
