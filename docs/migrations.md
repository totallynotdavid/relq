# Migrations

`relq-migrate` applies raw SQL migration files to a SQLite or PostgreSQL
database. It is separate from the query builders and does not import them.
[Installation](./installation.md) lists its extras.

## Files and ordering

Put the migrations in one folder. Each file has a four-digit number and a
lower-case, underscore-separated name:

```text
migrations/
  0001_create_users.sql
  0002_add_user_status.sql
```

Files are applied in numeric order. The sequence must start at `0001` and have
no gaps. Non-SQL files in the folder are ignored, but every SQL file must follow
the filename convention, and a misnamed one is an error. Migrations are
forward-only. To change an applied schema, add a new file instead of editing or
deleting an old one. Each applied file is recorded with a SHA-256 checksum, so
editing one makes the next run fail.

There are no down-migrations.

## Command line

`relq-migrate` applies the folder and, with `--codegen`, regenerates the schema
module from the migrated database in the same run:

```bash
uv tool run "relq-migrate[codegen]" sqlite app.db migrations --codegen src/my_app/db_schema.py
uv tool run "relq-migrate[codegen,postgres]" postgres "$DATABASE_URL" migrations \
  --codegen src/my_app/db_schema.py --schema public
```

The history table is left out of the generated module. Without `--codegen`, the
command only migrates, and `--config` and `--check` are errors. A failure it
expects prints one line, `relq-migrate: <message>`, and exits non-zero. These
are a rejected migration or history, an unreadable folder or database, and a
PostgreSQL URL that is malformed or cannot be reached. A wrong option prints the
usage message instead.

`--check` is for CI. With `--codegen OUTPUT --check` nothing is applied or
written: the command fails when migrations are pending or `OUTPUT` is stale. On
SQLite it opens the database read-only, and on PostgreSQL it sets
`default_transaction_read_only`:

```bash
uv tool run "relq-migrate[codegen]" sqlite app.db migrations \
  --codegen src/my_app/db_schema.py --check
```

| Option                | Applies to | Meaning                                                                                                         |
| --------------------- | ---------- | --------------------------------------------------------------------------------------------------------------- |
| `--table NAME`        | both       | History table name (default `relq_migrations`).                                                                 |
| `--busy-timeout SECS` | SQLite     | How long to wait for another writer's lock (default 30).                                                        |
| `--schema NAME`       | PostgreSQL | Schema to generate from (default `public`).                                                                     |
| `--codegen OUTPUT`    | both       | Schema module to write after migrating.                                                                         |
| `--config REFERENCE`  | both       | Type mapping for codegen, the same value as `relq-codegen --config`. See [Code generation](./codegen.md#types). |
| `--check`             | both       | With `--codegen`, change nothing and fail on pending migrations or a stale module.                              |

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

The migrator opens `BEGIN IMMEDIATE` before each migration. Its write lock
covers the migration's history read, DDL, and history record. Concurrent
migrators serialize, and none applies a file twice, as long as the peer releases
its write lock within `busy_timeout`. If a later file fails, earlier successful
files stay committed, as on PostgreSQL.

`busy_timeout` is how many seconds the migrator waits for another writer's lock
before failing, 30 by default. It applies for the whole run, and the
connection's own setting is restored afterwards. Set it longer than your slowest
migration, because a second migrator waits for the first one's whole
transaction. An expired timeout is reported as a database-locked error.

```python
SQLiteMigrator(connection, provider, busy_timeout=120).migrate_to_latest()
```

### Table rebuilds

SQLite's table-rebuild procedure drops and recreates a table that other tables
reference, so it needs foreign-key enforcement off. SQLite ignores
`PRAGMA foreign_keys = OFF` inside a transaction, so a migration asks the
migrator to do it. Put this directive in the file's leading comments:

```sql
-- relq: foreign_keys = off
create table users_new (id integer primary key, email text not null);
insert into users_new select id, email from users;
drop table users;
alter table users_new rename to users;
```

The migrator turns enforcement off before `BEGIN`, runs
`PRAGMA foreign_key_check` before `COMMIT`, and restores enforcement last. Any
violation rolls the migration back and names the first table affected. This is
the only directive. Any other `-- relq:` comment in the leading comments is an
error. PostgreSQL has no such pragma, so `PostgresMigrator` rejects a file that
carries the directive.

The adapter supports SQLite's legacy transaction-control mode and
`autocommit=True`. It issues its own `BEGIN IMMEDIATE`, `COMMIT`, and `ROLLBACK`
in both modes. `autocommit=False` raises an error. Do not call the migrator on a
connection that already has a caller-owned transaction. Both adapters raise
`RuntimeError` in that case.

Migration files must not contain transaction-control statements: `BEGIN`,
`COMMIT`, `ROLLBACK`, `SAVEPOINT`, `RELEASE`, or `END` and `END TRANSACTION`
used as transaction control. The migrator owns those boundaries and rejects the
file before applying it. The `END` that closes a SQLite trigger body is allowed.
The same rule applies to PostgreSQL migrations.

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
files stay applied. The adapter also accepts a pool, and holds one connection
for the whole run. A direct connection must not already be inside a caller-owned
transaction. The migrator raises instead of reporting a success that an outer
rollback could undo. The adapter imports without `asyncpg`, but running it
requires the `postgres` extra.

If you need legacy backslash escapes in plain strings, set
`standard_conforming_strings` on the session before running. Migration SQL
cannot change that setting, because doing so would make statement boundaries
ambiguous.

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
   lock, or one SQLite connection owns the `BEGIN IMMEDIATE` write lock. It may
   create the history table, read history, and determine the pending suffix.
   Other migrators wait for the lock or fail when their SQLite busy timeout
   expires.
3. **Migration transaction:** the lock owner has one pending file open in a
   transaction. Its DDL and its history row are uncommitted together, and no
   other migrator may apply that file. Migration SQL cannot change the
   transaction boundary.
4. **Committed or rolled back:** on success, the DDL and the new history row
   commit together, which extends the prefix by one, and the next file can
   begin. On failure, both roll back, earlier committed files remain, and the
   report marks the failed file `Error` and later files `NotExecuted`. The lock
   is then released.

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

`pending()` returns the names that `migrate_to_latest()` would apply and writes
nothing. It works on a read-only connection and raises `MigrationError` for the
history problems a run would also reject. A database without a history table has
every migration pending. `PostgresMigrator.pending()` is a coroutine.

The history table is named `relq_migrations` by default. Pass a different simple
identifier as `table_name` to either migrator to change it.
