"""relq-migrate against PostgreSQL: migrate, regenerate, and verify in one command."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest

from tests.integration.postgres.support import configured_harness

pytestmark = pytest.mark.skipif(
    os.environ.get("RELQ_TEST_POSTGRES_DSN") is None,
    reason="set RELQ_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)


async def _run(*arguments: str | Path) -> subprocess.CompletedProcess[str]:
    return await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "relq_migrate", "postgres", *map(str, arguments)],
        capture_output=True,
        check=False,
        text=True,
    )


async def test_migrate_then_codegen_leaves_out_the_history_table_and_checks_freshness(
    postgres_codegen_admin: asyncpg.Connection, postgres_codegen_schema: str, tmp_path: Path
) -> None:
    # The migrator creates its history table in the first schema of the search path.
    await postgres_codegen_admin.execute(
        f"alter role current_user set search_path = {postgres_codegen_schema}"
    )
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "0001_accounts.sql").write_text(
        f"create table {postgres_codegen_schema}.accounts (id integer primary key, name text);"
    )
    output = tmp_path / "schema.py"
    dsn = configured_harness().dsn
    arguments = [dsn, folder, "--schema", postgres_codegen_schema, "--codegen", output]
    try:
        migrated = await _run(*arguments)
        assert migrated.returncode == 0, migrated.stderr
        generated = output.read_text()
        assert "class Accounts(Table" in generated
        assert "relq_migrations" not in generated

        assert (await _run(*arguments, "--check")).returncode == 0

        output.write_text(generated.replace("name", "title"))
        stale = await _run(*arguments, "--check")
        assert stale.returncode == 1
        assert stale.stderr.splitlines() == [
            f"relq-migrate: codegen: generated schema is stale: {output}"
        ]

        output.write_text(generated)
        (folder / "0002_email.sql").write_text(
            f"alter table {postgres_codegen_schema}.accounts add column email text;"
        )
        pending = await _run(*arguments, "--check")
        assert pending.returncode == 1
        assert pending.stderr.splitlines() == [
            "relq-migrate: 1 migration(s) pending (0002_email.sql); run without --check to apply them"
        ]
        columns = await postgres_codegen_admin.fetch(
            "select column_name from information_schema.columns "
            "where table_schema = $1 and table_name = 'accounts'",
            postgres_codegen_schema,
        )
        assert {record["column_name"] for record in columns} == {"id", "name"}
        assert output.read_text() == generated
    finally:
        await postgres_codegen_admin.execute("alter role current_user reset search_path")


async def test_a_failing_migration_is_one_line(tmp_path: Path) -> None:
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "0001_bad.sql").write_text("create tabel broken (id integer);")

    result = await _run(configured_harness().dsn, folder, "--table", "cli_bad_history")

    assert result.returncode == 1
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("relq-migrate: migration '0001_bad.sql' failed: ")


async def test_an_unreachable_server_is_one_line(tmp_path: Path) -> None:
    folder = tmp_path / "migrations"
    folder.mkdir()

    result = await _run("postgresql://relq@127.0.0.1:1/none", folder)

    assert result.returncode == 1
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("relq-migrate: cannot connect to PostgreSQL: ")


async def test_codegen_with_an_unreachable_server_is_one_line(tmp_path: Path) -> None:
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "relq_codegen",
            "postgres",
            "postgresql://relq@127.0.0.1:1/none",
            str(tmp_path / "schema.py"),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    lines = result.stderr.splitlines()
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith("relq-codegen: ")
