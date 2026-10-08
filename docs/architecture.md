# Architecture

This is a map of relq's packages and modules for contributors. None of it is
public API. [Overview](./overview.md) describes the public model.

## Packages

```text
packages/relq-core       public builders and types, private AST, semantic
                         analysis, validation, SQL compilation for a fixed
                         dialect, row adapters, and relq.postgres, the
                         PostgreSQL-only expressions
packages/relq-sqlite     sqlite3 executor
packages/relq-postgres   asyncpg executor and PostgreSQL-only decoding
packages/relq-codegen    schema introspection, type mapping, source rendering,
                         and the CLI. It does not use the query builder.
packages/relq-migrate    forward-only SQL migrations for SQLite and
                         PostgreSQL, and the relq-migrate CLI. It does not use
                         the query builder. Its codegen extra depends on
                         relq-codegen for the CLI's --codegen option.
tests/                   unit tests, the shared relational matrix, and
                         PostgreSQL integration tests
scripts/                 the ephemeral PostgreSQL and clean-install harnesses
```

Modules and packages whose names start with an underscore are private. The
executors import the private `relq._compiler.api`, `relq._execution`, and
`relq._batching`. `relq/__init__.py` re-exports the public surface from `dml`,
`expressions`, `query`, and `rows`.

## relq-core modules

Each line lists what a module imports from relq. A module with no imports is a
leaf.

```text
_ast.py                    : leaf. Frozen dataclasses (the Node/QueryNode union).
rows.py                    : leaf. RowAdapter, Decoder, and the built-in decoders.
_temporal.py               : leaf. The temporal vocabulary shared by builders and validation.
_batching.py               : leaf. Splits a row stream under a parameter ceiling, for row_batches.
_node_value.py             → _ast
expressions/ordering.py    → _ast, _node_value
expressions/core.py        → _ast, _node_value, _query (select_node), _temporal,
                             expressions/ordering, rows
                             query (SelectQuery) for type checking and inside one function
expressions/relations.py   → _ast, _node_value, expressions/core
expressions/analytics.py   → _ast, _node_value, expressions/core, expressions/ordering
expressions/__init__.py    → re-exports the four above and the _temporal enums
_analysis/walk.py          → _ast
_analysis/{ctes,scopes,sources}.py → _ast, _analysis/walk
_analysis/nullability.py   → _ast
_compiler/_model.py        : leaf. Dialect and CompiledQuery dataclasses.
_compiler/_render.py       → _ast, _compiler/_model
_compiler/grouping.py      → _ast, _analysis/walk
_compiler/validation.py    → _analysis/*, _ast, _compiler/grouping, _compiler/_model,
                             _temporal, rows
_compiler/api.py           → _compiler/_model, _compiler/_render, _compiler/validation, _query
_compiler/__init__.py      → re-exports CompiledQuery and compile_{sqlite,postgres}
_query.py                  → _ast, rows [type checking only: dml, query]
dml.py                     → _ast, _node_value, _query, expressions,
                             expressions/relations, rows, query (SelectQuery)
query.py                   → _ast, _node_value, _query, expressions,
                             expressions/relations, rows, dml (ModifyingCteBody)
postgres.py                → _ast, _node_value, expressions, rows
_execution.py              → _ast, _query, dml, query
relq/__init__.py           → dml, expressions, query, rows
```

`_analysis` modules do not import each other, except for `walk`.

`dml.py` and `query.py` refer to each other in their signatures. Each imports
the other at the end of its module, after defining its own names, so that
`typing.get_type_hints()` resolves both public signatures without a
`TYPE_CHECKING` import. `dml.py` defines `ModifyingCteBody`, the union that
`with_modifying()` accepts.

`postgres.py` is not re-exported by `relq/__init__.py`.

## Executors

`relq-sqlite` and `relq-postgres` are separate packages with no shared base
class. Both follow the same transaction model, and their bookkeeping is
parallel.

Each physical connection has one registry, shared by every `Database` wrapper
around it. It holds an ordered list of entries (controlled transactions and
savepoints), a stack of transactions, and the names of the active savepoints. It
is created on first use and dropped when it is empty. The registry is the only
record of a handle's descendants.

- A handle is usable until it is closed or invalidated. Either one removes the
  handle and its savepoint names from the registry, and every later operation on
  it raises `TransactionUnavailableError`.
- A transaction `commit()` or `rollback()` invalidates every later entry before
  it sends the command to the driver. A savepoint `release()` or `rollback()`
  invalidates them after the command succeeds. A savepoint `rollback()` leaves
  that savepoint usable until it is released.
- When a transaction command fails, recovery invalidates every handle on the
  connection and then resets the connection.
- Registry methods are synchronous, so event-loop tasks cannot interleave them.
  The drivers still serialize operations on a connection, and relq does not make
  concurrent raw driver operations safe.
- PostgreSQL also keeps a weak set of the constructed, unclosed handles per
  connection. A handle enters it in its constructor and leaves it in `_close()`.
  Recovery snapshots the set before it invalidates the handles.

The user-facing behavior is in [Execution](./execution.md#transactions).

## relq-codegen modules

`config.py` turns a TOML file or a Python module into a `CodegenConfig`.
`errors.py` defines `CodegenError`, the failure a user can fix by changing their
schema or type mapping. Both CLIs print it as one line, as they do `OSError` and
`sqlite3.Error`. Any other exception keeps its traceback. `generate.py` holds
`write_module`, which decides whether an existing module is current by comparing
syntax trees. It is shared by `relq-codegen` and `relq-migrate --codegen`.

## relq-codegen names

`relq_codegen/render.py` allocates every generated name in one pass before it
renders. The renaming rules are in [Code generation](./codegen.md#names).
