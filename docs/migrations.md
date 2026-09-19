# Migrations

`relq-migrate` manages the schema lifecycle separately from relq's query
builders. It applies raw SQL migration files, because DDL is where SQL text is
appropriate. Ordinary relq queries stay structured and parameterized.

## Files and ordering

Put the migrations in one folder. Each file has a four-digit number and a
lower-case, underscore-separated name:

```text
migrations/
  0001_create_users.sql
  0002_add_user_status.sql
```

Files are applied in numeric order. The sequence must start at `0001` and have no
gaps. Non-SQL files in the folder are ignored, but every SQL file must follow the
filename convention, and a misnamed one is an error. Migrations are forward-only.
To change an applied schema, add a new file instead of editing or deleting an
old one. Each applied file is recorded with a SHA-256 checksum, so editing one
makes the next run fail.

There are no down-migrations, because arbitrary DDL cannot be undone safely or
completely. An append-only history also keeps the schema history auditable.

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

The migrator opens `BEGIN IMMEDIATE` before each migration. SQLite ignores
`PRAGMA foreign_keys = OFF` inside a transaction, so migration SQL cannot turn
foreign-key enforcement off. The standard table-rebuild recipe drops a parent
table, and with `ON DELETE CASCADE` children that deletes child rows. Do not use
it under this adapter. Keep enforcement on and use a rebuild sequence that is
valid with it, or do that operation outside the migrator.

The `BEGIN IMMEDIATE` write lock covers each migration's history read, DDL, and
history record. Concurrent migrators serialize, and none applies a file twice, as
long as the peer releases its write lock within the connection's busy timeout.
If another migrator may run at the same time, set `timeout` on `sqlite3.connect`
or `PRAGMA busy_timeout` longer than your slowest migration. An expired timeout is
reported as a database-locked error. If a later file fails, earlier successful
files stay committed, as on PostgreSQL.

The adapter supports SQLite's legacy transaction-control mode and
`autocommit=True`. It issues its own `BEGIN IMMEDIATE`, `COMMIT`, and `ROLLBACK`
in both modes. `autocommit=False` raises an error. Do not call the migrator on a
connection that already has a caller-owned transaction. Both adapters raise
`RuntimeError` in that case.

Migration files must not contain transaction-control statements: `BEGIN`,
`COMMIT`, `ROLLBACK`, `SAVEPOINT`, `RELEASE`, or `END` and `END TRANSACTION` used
as transaction control. The migrator owns those boundaries and rejects the file
before applying it. The `END` that closes a SQLite trigger body is allowed. The
same rule applies to PostgreSQL migrations.

## PostgreSQL

Install the optional PostgreSQL dependency first:

```bash
uv add "relq-migrate[postgres]"
```

```python
import asyncio
import asyncpg
from pathlib import Path

from relq_migrate import FileMigrationProvider, PostgresMigrator

database_url = "postgresql://user:password@localhost/app"


async def main() -> None:
    connection = await asyncpg.connect(database_url)
    try:
        report = await PostgresMigrator(
            connection, FileMigrationProvider(Path("migrations"))
        ).migrate_to_latest()
        if report.error is not None:
            raise report.error
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
```

The PostgreSQL adapter takes a session advisory lock for the history table and
runs each migration and its history record in one transaction. A failed file is
reported as `Error` and later files as `NotExecuted`, while earlier successful
files stay applied. The adapter also accepts a pool, and holds one connection for
the whole run. A direct connection must not already be inside a caller-owned
transaction. The migrator raises instead of reporting a success that an outer
rollback could undo. The adapter imports without `asyncpg`, but running it
requires the `postgres` extra.

If you need legacy backslash escapes in plain strings, set
`standard_conforming_strings` on the session before running. Migration SQL cannot
change that setting, because doing so would make statement boundaries ambiguous.

## History and lock invariants

The migration state is an ordered prefix of the provider's files plus the schema
changes that prefix recorded. The history table is append-only. Each row holds
one file name and checksum, and the applied rows must be a contiguous prefix
whose checksums still match the files. Only the migrator appends rows.
Applications must not edit, delete, or insert history rows.

A run has four phases:

1. **Idle:** no migrator owns the lock. The committed schema and history table
   agree on the applied prefix.
2. **Locked and reading:** exactly one PostgreSQL session owns the advisory
   lock, or one SQLite connection owns the `BEGIN IMMEDIATE` write lock. It
   may create the history table, read history, and determine the pending
   suffix. Other migrators wait for the lock or fail when their SQLite busy
   timeout expires.
3. **Migration transaction:** the lock owner has one pending file open in a
   transaction. Its DDL and its history row are uncommitted together, and no
   other migrator may apply that file. Migration SQL cannot change the
   transaction boundary.
4. **Committed or rolled back:** on success, the DDL and the new history row
   commit together, which extends the prefix by one, and the next file can begin.
   On failure, both roll back, earlier committed files remain, and the report
   marks the failed file `Error` and later files `NotExecuted`. The lock is then
   released.

A run with nothing to do takes the lock, sees the complete prefix, and releases
the lock without changing anything. A concurrent run proceeds only after the
first lock owner commits or rolls back. It then re-reads the history and either
skips the newly committed files or applies the remaining ones.

PostgreSQL resolves the history table's schema on the first use of a physical
session and pins it. Later migrator instances and pool acquisitions on that
session use the same qualified name, so a `search_path` change made by a
migration cannot redirect history reads or writes. The advisory-lock key derives
from the same schema and table identity.

## Reports

`migrate_to_latest()` returns a `MigrationReport` with `error` and `results`.
Each result has a `migration_name`, a status of `Success`, `Error`, or
`NotExecuted`, and the exception when the status is `Error`. Files that were
already applied do not appear in a later report. A concurrent run that loses the
race can report `NotExecuted` for files the peer applied before it got the write
lock. Those files were skipped. They do not follow a migration error.

```python
for result in report.results:
    print(result.migration_name, result.status)
if report.error is not None:
    # The per-file result identifies where the run stopped.
    raise report.error
```

The history table is named `relq_migrations` by default. Pass a different simple
identifier as `table_name` to either migrator to change it.
