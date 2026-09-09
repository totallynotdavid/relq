# Migrations

`relq-migrate` owns schema lifecycle separately from relq's query builders. It
accepts raw SQL migration files because DDL is the boundary where SQL text is
appropriate; ordinary relq queries remain structured and parameterized.

## Files and ordering

Create a migration folder containing four-digit, lower-case, underscore-named
files:

```text
migrations/
  0001_create_users.sql
  0002_add_user_status.sql
```

Files are applied in numeric order. The sequence must start at `0001` and be
contiguous. Non-migration files in the folder are ignored. A migration is
forward-only: change an already-applied schema by adding a new file rather
than editing or deleting an old one. Applied files are recorded with a SHA-256
checksum, so editing one causes the migration run to fail loudly.

This is deliberate: arbitrary DDL cannot be safely or completely undone, so
automatic down-migrations would make deployment recovery less predictable.
The append-only policy follows rqueue's runner and keeps the schema history
auditable.

## SQLite

```python
from pathlib import Path
import sqlite3

from relq_migrate import FileMigrationProvider
from relq_migrate import SQLiteMigrator

connection = sqlite3.connect("app.db")
provider = FileMigrationProvider(Path("migrations"))
report = SQLiteMigrator(connection, provider).migrate_to_latest()

if report.error is not None:
    raise report.error
```

SQLite takes a `BEGIN IMMEDIATE` write lock for each migration's history-read,
DDL, and history-record transaction. Concurrent migration attempts therefore
serialize without double-applying a file. Earlier successful files remain
committed if a later file fails, matching PostgreSQL. Do not call the migrator
while the connection already owns a transaction. Migration files must not issue
`BEGIN`, `COMMIT`, `END`, `ROLLBACK`, or other transaction-control statements;
the migrator owns those boundaries and reports an error if a statement ends its
transaction. The same rule applies to PostgreSQL migrations.

## PostgreSQL

Install the optional PostgreSQL dependency first:

```bash
uv add "relq-migrate[postgres]"
```

```python
import asyncpg
from pathlib import Path

from relq_migrate import FileMigrationProvider, PostgresMigrator

connection = await asyncpg.connect(database_url)
try:
    report = await PostgresMigrator(
        connection, FileMigrationProvider(Path("migrations"))
    ).migrate_to_latest()
    if report.error is not None:
        raise report.error
finally:
    await connection.close()
```

The PostgreSQL adapter takes a session advisory lock for the history table and
runs each migration plus its history record in one transaction. A failed file
is reported as `Error`; later files are `NotExecuted`, while earlier successful
files remain applied. A pool is also accepted and holds one connection for the
whole migration run. A direct connection must not already be inside a caller-
owned transaction; the migrator raises instead of returning a success that an
outer rollback could invalidate.

## Reports

`migrate_to_latest()` returns a `MigrationReport` with `error` and `results`,
following Kysely's useful result shape. Each result has `migration_name`, a
`status` of `Success`, `Error`, or `NotExecuted`, and the exception on an error
result. Already-applied files do not appear in a later no-op report.

```python
for result in report.results:
    print(result.migration_name, result.status)
if report.error is not None:
    # The per-file result identifies where the run stopped.
    raise report.error
```

The history table is named `relq_migrations` by default. Pass a different
simple identifier as `table_name` to either migrator when an application needs
another name.
