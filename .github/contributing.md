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
mise run install
```

`mise.toml` pins the Python, uv, oxfmt, and PostgreSQL versions used for
development.

## Checks

```bash
mise run install           # install every package from uv.lock
mise run fix               # apply safe ruff fixes and format all files
mise run lint              # Ruff plus oxfmt for Markdown, YAML and JSON
mise run typecheck         # basedpyright, strict mode
mise run test              # SQLite and backend-independent tests
mise run test:integration  # PostgreSQL tests on an ephemeral server
mise run build             # build and smoke-test every distribution
mise run check             # lint, typecheck, test and build; run it before submitting
```

`mise run build` installs the built wheels into fresh SQLite-only and
PostgreSQL-only environments. That step catches a PostgreSQL dependency leaking
into the SQLite-only install. `mise run check` starts no server, so run
`mise run test:integration` as well when a change touches PostgreSQL code.

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
which `mise run test:integration` does for you by starting an ephemeral server.
When you set it yourself, the task uses that server instead. Set
`RELQ_KEEP_TEST_SCHEMA=1` to keep the test schema after a local run.

## Releases

[Releasing](../docs/releasing.md) describes how a release is cut.
