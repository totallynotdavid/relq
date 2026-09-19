# Contributing to relq

## Set up

```bash
git clone https://github.com/totallynotdavid/relq
cd relq
mise install
uv sync --locked --all-packages
```

`mise.toml` pins the Python, uv, and PostgreSQL versions used for development.

## Workspace layout

See [architecture.md](./architecture.md) for the package breakdown and
relq-core's module dependency graph.

## Checks

```bash
mise format          # apply formatting
mise format-check    # formatting gate
mise lint             # Ruff
mise typecheck        # basedpyright strict mode
mise test:sqlite      # SQLite + backend-independent suite
mise test:postgres    # ephemeral PostgreSQL integration suite
mise test             # both suites
mise build            # build all workspace distributions
mise release          # clean wheel install/import/codegen verification
mise check            # every gate above; required before submitting a change
```

`mise check` starts an ephemeral PostgreSQL instance, runs the full SQLite and
PostgreSQL test matrix, and installs the built wheels into fresh SQLite-only and
PostgreSQL-only environments. That last step catches a PostgreSQL dependency
leaking into the SQLite-only install.

## Tests

- `tests/compiler`: builders, query composition, and compiler contracts.
- `tests/semantics`: query analysis and AST traversal.
- `tests/codegen`: SQLite rendering and CLI freshness.
- `tests/migrate`: migration providers and the PostgreSQL migrator.
- `tests/typing`: the type-checker fixtures. `test_typing_fixtures.py` runs
  them.
- `tests/integration/sqlite` and `tests/integration/postgres`: real-driver
  execution, DML, decoding, catalog, and analytic contracts.

Shared schemas live in `tests/fixtures.py`, and `tests/snapshots` holds the
generated schema modules that the codegen tests compare against. The relational
matrix in `tests/relational_matrix.py` contains assertions only, and each backend
supplies its own DDL and data through a `matrix_fixture.py` next to its tests.
PostgreSQL lifecycle cleanup therefore stays independent of the behavior tests.
The PostgreSQL integration tests run only when `RELQ_TEST_POSTGRES_DSN` is set.
`RELQ_KEEP_TEST_SCHEMA=1` keeps the test schema after a local run for debugging.
