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
tests/                   contract-focused unit tests, shared relational matrix,
                         and PostgreSQL integration tests
scripts/                 ephemeral PostgreSQL and clean-wheel-install harnesses
```

`relq-sqlite` and `relq-postgres` are deliberately separate, small executor
packages rather than sharing a base class: their cursor and transaction
semantics genuinely differ (sync vs async, `sqlite3` vs `asyncpg`
prepared-statement caching).

The executors nevertheless use the same transaction-boundary model. Each
maintains an ordered transaction stack and one registry for controlled
transactions and savepoints, invalidates descendants when a database boundary
destroys them, and reserves active savepoint names. The registry is the source
of truth for descendant invalidation; there is no second child tree that can
drift out of sync. The lifecycle invariant is:

1. A handle is `unregistered` until its transaction boundary has started and
   its constructor has registered it. It is then `open` and appears in the
   registry for its physical connection. A successful transaction-level
   `commit()` or `rollback()` moves it to `closed`; a successful savepoint
   `release()` does the same, while savepoint-level `rollback()` leaves that
   savepoint open and usable until it is released. Descendant invalidation or
   connection recovery moves a handle to `invalidated`. Both terminal states
   remove the handle and its active savepoint-name reservations, and all later
   operations reject the handle.
2. `begin()` and `savepoint()` append entries only after their driver boundary
   has started successfully. `commit()` and `rollback()` invalidate later
   entries in registry order as part of their boundary path; transaction
   boundaries pre-invalidate descendants before their driver command, while
   savepoint rollback/release invalidates them after the driver command
   succeeds. A failed transaction boundary invalidates the whole registry
   before connection recovery.
3. The state registry is keyed by physical connection identity, not by a
   `Database` wrapper. Thus wrappers around one connection share ordering,
   name reservations, and invalidation. When its registry becomes empty, its
   per-connection state is removed. PostgreSQL's additional weak set contains
   exactly the handles that are open and fully constructed for that physical
   connection; construction is its only registration point and `_close()` its
   only explicit removal point. Recovery snapshots that set before invalidating
   and releasing the captured handles.
4. Registry mutations contain no await point, so an event-loop task cannot
   interleave them. For overlapping operations, list order is the order in
   which successful boundaries register, and a destructive boundary invalidates
   every later entry present when it performs that mutation. The drivers still
   serialize operations on a connection; relq does not make concurrent raw
   driver operations safe, and a driver failure follows the recovery rules
   above.

The implementations remain separate only at the driver-facing operations and
observer timing; the bookkeeping terminology and lifecycle rules are
intentionally parallel.

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
