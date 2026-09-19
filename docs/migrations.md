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
contiguous. Non-SQL files in the folder are ignored, but every SQL file must
use the migration filename convention; a misnamed SQL file is an error. A
migration is forward-only: change an already-applied schema by adding a new
file rather than editing or deleting an old one. Applied files are recorded
with a SHA-256 checksum, so editing one causes the migration run to fail
loudly.

This is deliberate: arbitrary DDL cannot be safely or completely undone, so
automatic down-migrations would make deployment recovery less predictable.
The append-only policy keeps deployment recovery predictable and the schema
history auditable.

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

Important: the migrator opens `BEGIN IMMEDIATE` before each migration. SQLite
therefore ignores `PRAGMA foreign_keys = OFF` issued by migration SQL, because
that setting cannot change inside a transaction. The standard table-rebuild
recipe that drops a parent table with `ON DELETE CASCADE` children is not safe
under this adapter: it can delete child rows. Keep foreign-key enforcement on
and use a rebuild sequence that is valid with it, or perform that specialized
operation outside this migrator.

SQLite takes a `BEGIN IMMEDIATE` write lock for each migration's history-read,
DDL, and history-record transaction. Concurrent migration attempts therefore
serialize without double-applying a file while the peer releases its write
lock within the connection's busy timeout. Set `timeout` on `sqlite3.connect`
(or `PRAGMA busy_timeout`) longer than the slowest expected migration if
another migrator may be running concurrently; an expired timeout is reported
as a database-locked error. Earlier successful files remain committed if a
later file fails, matching PostgreSQL.

The adapter supports SQLite's legacy transaction-control mode and
`autocommit=True`; it owns explicit `BEGIN IMMEDIATE`, `COMMIT`, and `ROLLBACK`
boundaries in both modes. `autocommit=False` is unsupported and raises a clear
error. Do not call the migrator while the connection already owns a caller
transaction; both adapters raise `RuntimeError` for that precondition.

Migration files must not issue transaction-control statements such as `BEGIN`,
`COMMIT`, `ROLLBACK`, `SAVEPOINT`, `RELEASE`, or transaction-control
`END`/`END TRANSACTION`; the migrator owns those boundaries and rejects them
before applying the migration. This does not prohibit the `END` keyword that
closes a SQLite trigger body. The same rule applies to PostgreSQL migrations.

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
runs each migration plus its history record in one transaction. A failed file
is reported as `Error`; later files are `NotExecuted`, while earlier successful
files remain applied. A pool is also accepted and holds one connection for the
whole migration run. A direct connection must not already be inside a caller-
owned transaction; the migrator raises instead of returning a success that an
outer rollback could invalidate. The PostgreSQL adapter is importable without
`asyncpg`, but running it requires the optional `postgres` extra.
Configure `standard_conforming_strings` on the session before running if legacy
plain-string backslash escapes are required; migration SQL itself may not
change that setting because it would make statement boundaries ambiguous.

## History and lock invariants

The migration state is an ordered prefix of the provider's files plus the
database schema changes recorded by that prefix. The history table is append-
only: each row contains one file name and checksum, and applied rows must be a
contiguous prefix whose checksums still match the files. The migrator is the
only component allowed to append a row; applications must not edit, delete, or
insert history rows directly.

There are four valid phases for a run:

1. **Idle:** no migrator owns the lock. The committed schema and history table
   agree on the applied prefix.
2. **Locked and reading:** exactly one PostgreSQL session owns the advisory
   lock, or one SQLite connection owns the `BEGIN IMMEDIATE` write lock. It
   may create the history table, read history, and determine the pending
   suffix. Other migrators wait for the lock or fail when their SQLite busy
   timeout expires.
3. **Migration transaction:** the lock owner has one pending file open in a
   transaction. Its DDL and history-row insert are uncommitted together; no
   other migrator may apply that file. Migration SQL may not change the
   transaction boundary.
4. **Committed or rolled back:** on success, the DDL and new history row commit
   together, extending the prefix by one, and the next file may begin. On
   failure, the current transaction rolls back together, earlier committed
   files remain, and the report marks the failed file `Error` and later files
   `NotExecuted`. The lock is then released.

A no-op run takes the lock, observes the complete prefix, and releases it
without changing either state. A concurrent run can proceed only after the
first lock owner commits or rolls back; it then re-reads history and either
skips the newly committed files or applies the remaining suffix. A caller-owned
transaction is rejected, so an outer rollback cannot erase a reported success.
PostgreSQL resolves and pins the history table's schema on the first use of a
physical PostgreSQL session, then uses that qualified name for later migrator
instances and pool connection acquisitions on that session. A migration's
persistent `search_path` changes therefore cannot redirect history reads or
writes. PostgreSQL's advisory-lock key is derived from that same physical
schema/table identity.

## Reports

`migrate_to_latest()` returns a `MigrationReport` with `error` and `results`.
Each result has `migration_name`, a status of `Success`, `Error`, or
`NotExecuted`, and the exception on an error result. Already-applied files do
not appear in a later sequential no-op report. A concurrent run that loses the
race may report `NotExecuted` for files the peer applied before it acquired the
write lock; these are skipped files, not later files after a migration error.

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
