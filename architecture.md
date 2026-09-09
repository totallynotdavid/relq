# Architecture

This describes relq's internal package and module layout, useful when navigating
the codebase but not part of the public API. For the public model (what the
layers mean and why they're split this way), see
[How relq works](./docs/how-relq-works.md).

## Package responsibilities

```text
packages/relq-core       public types/builders, private AST, semantic analysis,
                         validation, fixed-dialect SQL compilation, row adapters,
                         and relq.postgres: the PostgreSQL-only expression surface
packages/relq-sqlite     sqlite3 execution boundary
packages/relq-postgres   asyncpg execution boundary and PostgreSQL-only decode
packages/relq-codegen    schema introspection, type mapping, deterministic source
                         rendering, and CLI; no query-builder ownership
packages/relq-migrate    forward-only raw-DDL migrations with separate SQLite
                         and PostgreSQL adapters; no query-builder ownership
tests/                   contract-focused unit tests, shared relational matrix,
                         and PostgreSQL integration tests
scripts/                 ephemeral PostgreSQL and clean-wheel-install harnesses
```

`relq-sqlite` and `relq-postgres` are deliberately separate, small executor
packages rather than sharing a base class: their cursor and transaction
semantics genuinely differ (sync vs async, `sqlite3` vs `asyncpg`
prepared-statement caching).

## relq-core module dependency graph

```text
_ast.py                    : leaf. Pure frozen dataclasses (Node/QueryNode union). No relq imports.
rows.py                    : leaf. RowAdapter/Decoder protocol + all builtin decoders. No relq imports.
expressions/ordering.py    → _ast
expressions/core.py        → _ast, _query (select_node), expressions/ordering
expressions/relations.py   → _ast, expressions/core, rows (JsonValue, for json_column)
expressions/analytics.py   → _ast, expressions/core, expressions/ordering
expressions/__init__.py    → re-exports the four above
_analysis/{ctes,nullability,scopes,sources}.py → _ast (+ walk) only (no cross-imports between these four)
_compiler/_model.py        : leaf. Dialect/CompiledQuery dataclasses.
_compiler/_render.py       → _ast, _compiler/_model
_compiler/grouping.py      → _ast
_compiler/validation.py    → _analysis/*, _ast, _compiler/grouping
_query.py                  → _ast, rows  [+ TYPE_CHECKING-only: dml, query]
dml.py                     → _ast, _query, expressions, rows, query (SelectQuery, for from_select)
query.py                   → _ast, _query, expressions, rows, dml (CteQuery, for with_)
                             These two are mutually recursive at the type level, so each binds the
                             other's names at the END of its module, after defining its own. That
                             keeps both public signatures resolvable by typing.get_type_hints()
                             instead of hiding one side behind TYPE_CHECKING. dml.py owns the
                             CteQuery alias, the union with_() accepts.
postgres.py                → _ast, expressions, rows. PostgreSQL-only expressions; not re-exported by relq/__init__
_compiler/api.py           → _compiler/_model, _compiler/_render, _compiler/validation, _query, dml, query
_compiler/__init__.py      → re-exports _model.CompiledQuery, api.compile_{sqlite,postgres}
_execution.py              → _ast, _query, dml, query
relq/__init__.py           → dml, expressions, query, rows   (the entire public surface)
```

Only the schema, builder, and result-mapping layers are public. AST nodes,
rendering state, and compiler dialect objects never leak through a public
constructor, annotation, or accessor.

## Layer boundaries

Each layer keeps a single responsibility, and boundaries are deliberately not
merged for convenience:

- `expressions/`: scalar expressions, relation descriptors, ordering, and
  analytics are separate modules; this is not one god `expressions.py`.
- `_analysis/`: source scope, CTE scope, nested query scopes, and outer-join
  nullability are separate compiler passes, not one semantic junk drawer.
- `_compiler/`: rendering stays private and cohesive; it's split only when a
  sub-renderer earns its own responsibility or test seam.
- `rows.py`: adapters and built-in decoders share one explicit driver-trust
  boundary and stay together.
