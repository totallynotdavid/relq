# Contributing to relq

Thank you for considering a contribution. If the change is not small, open an
issue to discuss it first.

## The codebase

[Architecture](../docs/architecture.md) maps the packages and the modules of
relq-core.

## Set up

```bash
git clone https://github.com/totallynotdavid/relq
cd relq
mise install
uv sync --locked --all-packages
```

`mise.toml` pins the Python, uv, and PostgreSQL versions used for development.

## Checks

```bash
mise format          # apply formatting
mise format-check    # check formatting
mise lint            # Ruff
mise typecheck       # basedpyright, strict mode
mise test:sqlite     # SQLite and backend-independent tests
mise test:postgres   # PostgreSQL integration tests on an ephemeral server
mise test            # both test suites
mise build           # build all workspace distributions
mise release         # install the built wheels in clean environments
mise check           # every check above; run it before submitting a change
```

`mise check` starts an ephemeral PostgreSQL server, runs the SQLite and
PostgreSQL tests, and installs the built wheels into fresh SQLite-only and
PostgreSQL-only environments. The last step catches a PostgreSQL dependency
leaking into the SQLite-only install.

## Tests

| Directory                                                | Covers                                                         |
| -------------------------------------------------------- | -------------------------------------------------------------- |
| `tests/compiler`                                         | Builders, query composition, and compiler contracts.           |
| `tests/semantics`                                        | Query analysis and AST traversal.                              |
| `tests/codegen`                                          | SQLite rendering, type-mapping config, and CLI freshness.      |
| `tests/migrate`                                          | Migration providers and the `relq-migrate` command.            |
| `tests/typing`                                           | Type-checker fixtures, run by `tests/test_typing_fixtures.py`. |
| `tests/integration/sqlite`, `tests/integration/postgres` | Real-driver execution, DML, decoding, catalog, and analytics.  |

Shared schemas live in `tests/fixtures.py`. `tests/snapshots` holds the
generated schema modules that the codegen tests compare against.
`tests/relational_matrix.py` holds assertions only. Each backend supplies its
own DDL and data in a `matrix_fixture.py` next to its tests.

The PostgreSQL integration tests run only when `RELQ_TEST_POSTGRES_DSN` is set,
which `mise test:postgres` does for you. Set `RELQ_KEEP_TEST_SCHEMA=1` to keep
the test schema after a local run.

## Releases

[Releasing](../docs/releasing.md) describes how a release is cut.
