# Contributing to relq

## Set up

```bash
git clone https://github.com/totallynotdavid/relq
cd relq
mise install
uv sync --locked --all-packages
```

`mise.toml` pins the Python, uv, and PostgreSQL versions this project develops
against.

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

`mise check` provisions an ephemeral PostgreSQL instance, runs the complete
SQLite + PostgreSQL test matrix, and installs the built wheels into fresh
SQLite-only and PostgreSQL-only environments, so a PostgreSQL dependency can
never leak into the SQLite-only install path by accident.

## Tests

Test layout mirrors the runtime pipeline, not delivery order:

- `tests/contract`: SQLite-backed builder, DML, decoding, and compiler contracts
- `tests/semantics`: query analysis and validation, directly
- `tests/codegen`: SQLite rendering and CLI freshness
- `tests/integration/postgres`: real-driver harness, execution, catalog, and
  analytic contracts

Shared schemas live in `tests/fixtures`; portable relational DDL/data lives
beside each backend matrix adapter. The matrix itself contains assertions only,
so PostgreSQL lifecycle cleanup stays independent of behavioral tests.
PostgreSQL integration tests are opt-in through `RELQ_TEST_POSTGRES_DSN`;
`RELQ_KEEP_TEST_SCHEMA=1` preserves the test schema after a local run for
debugging.
