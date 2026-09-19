# Architecture

This describes relq's internal package and module layout. None of it is public
API. [How relq works](./docs/how-relq-works.md) describes the public model and
why it is split into layers.

## Package responsibilities

```text
packages/relq-core       public types/builders, private AST, semantic analysis,
                         validation, fixed-dialect SQL compilation, row adapters,
                         and relq.postgres: the PostgreSQL-only expression surface
packages/relq-sqlite     sqlite3 execution boundary
packages/relq-postgres   asyncpg execution boundary and PostgreSQL-only decode
packages/relq-codegen    schema introspection, type mapping, deterministic source
                         rendering, and CLI. No query builder.
packages/relq-migrate    forward-only raw-DDL migrations with separate SQLite
                         and PostgreSQL adapters. No query builder.
tests/                   contract-focused unit tests, shared relational matrix,
                         and PostgreSQL integration tests
scripts/                 ephemeral PostgreSQL and clean-wheel-install harnesses
```

`relq-sqlite` and `relq-postgres` are separate small packages with no shared base
class, because their cursor and transaction semantics differ. One is synchronous
and the other asynchronous, and `sqlite3` and `asyncpg` differ in
prepared-statement caching.

Both executors use the same transaction-boundary model. Each keeps an ordered
transaction stack and one registry for controlled transactions and savepoints.
Each invalidates descendants when a database boundary destroys them and reserves
the names of active savepoints. The registry is the only record of descendants,
so no second child tree can drift out of sync. The lifecycle rules are:

1. A handle is `unregistered` until its transaction boundary has started and its
   constructor has registered it. It is then `open` and appears in the registry
   for its physical connection. A successful transaction `commit()` or
   `rollback()` moves it to `closed`, and so does a successful savepoint
   `release()`. A savepoint `rollback()` leaves that savepoint open and usable
   until it is released. Descendant invalidation or connection recovery moves a
   handle to `invalidated`. Both terminal states remove the handle and its
   savepoint-name reservations, and every later operation on the handle is
   rejected.
2. `begin()` and `savepoint()` append entries only after the driver boundary has
   started. `commit()` and `rollback()` invalidate later entries in registry
   order. A transaction boundary invalidates its descendants before the driver
   command. A savepoint rollback or release invalidates them after the driver
   command succeeds. A failed transaction boundary invalidates the whole
   registry before connection recovery.
3. The registry is keyed by physical connection identity, not by a `Database`
   wrapper, so wrappers around one connection share ordering, name reservations,
   and invalidation. The per-connection state is removed when its registry
   becomes empty. PostgreSQL also keeps a weak set of the handles that are open
   and fully constructed for a connection. Construction is its only registration
   point and `_close()` its only removal point. Recovery snapshots that set
   before invalidating the captured handles and releasing them.
4. Registry mutations contain no await point, so event-loop tasks cannot
   interleave them. For overlapping operations, list order is the order in which
   successful boundaries register, and a destructive boundary invalidates every
   later entry present when it mutates the registry. The drivers still serialize
   operations on a connection. relq does not make concurrent raw driver
   operations safe, and a driver failure follows the recovery rules above.

The two implementations differ only in the driver-facing operations and in when
the observer is called. The bookkeeping and the lifecycle rules are parallel.

## relq-core module dependency graph

```text
_ast.py                    : leaf. Frozen dataclasses (the Node/QueryNode union). No relq imports.
rows.py                    : leaf. RowAdapter, Decoder, and the built-in decoders. No relq imports.
_temporal.py               : leaf. The closed temporal vocabulary shared by builders and validation.
_node_value.py             → _ast
expressions/ordering.py    → _ast, _node_value
expressions/core.py        → _ast, _node_value, _query (select_node), _temporal, expressions/ordering, rows
expressions/relations.py   → _ast, _node_value, expressions/core, rows (JsonValue, for json_column)
expressions/analytics.py   → _ast, _node_value, expressions/core, expressions/ordering
expressions/__init__.py    → re-exports the four above and the _temporal enums
_analysis/walk.py          → _ast
_analysis/{ctes,scopes,sources}.py → _ast, _analysis/walk
_analysis/nullability.py   → _ast
                             No _analysis module imports another except walk.
_compiler/_model.py        : leaf. Dialect and CompiledQuery dataclasses.
_compiler/_render.py       → _ast, _compiler/_model
_compiler/grouping.py      → _ast, _analysis/walk
_compiler/validation.py    → _analysis/*, _ast, _compiler/grouping, _compiler/_model, _temporal, rows
_query.py                  → _ast, rows [+ TYPE_CHECKING only: dml, query]
dml.py                     → _ast, _node_value, _query, expressions, rows, query (SelectQuery, for from_select)
query.py                   → _ast, _node_value, _query, expressions, rows, dml (CteQuery, for with_)
                             The two refer to each other in their signatures, so each imports
                             the other at the END of its module, after defining its own names.
                             That lets typing.get_type_hints() resolve both public signatures
                             without a TYPE_CHECKING import. dml.py owns the CteQuery alias,
                             the union that with_() accepts.
postgres.py                → _ast, _node_value, expressions, rows. PostgreSQL-only expressions, not re-exported by relq/__init__
_compiler/api.py           → _compiler/_model, _compiler/_render, _compiler/validation, _query
_compiler/__init__.py      → re-exports _model.CompiledQuery and api.compile_{sqlite,postgres}
_execution.py              → _ast, _query, dml
relq/__init__.py           → dml, expressions, query, rows   (the entire public surface)
```

Only the schema, builder, and result-mapping layers are public. AST nodes,
rendering state, and compiler dialect objects never appear in a public
constructor, annotation, or accessor.

## Layer boundaries

Each layer has one responsibility, and the boundaries are not merged for
convenience:

- `expressions/`: scalar expressions, relation descriptors, ordering, and
  analytics are separate modules.
- `_analysis/`: source scope, CTE scope, nested query scopes, and outer-join
  nullability are separate passes.
- `_compiler/`: rendering is private and cohesive. It splits only when a
  sub-renderer earns its own responsibility or test seam.
- `rows.py`: adapters and the built-in decoders share one driver-trust boundary
  and stay together.
