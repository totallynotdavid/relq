"""relq-migrate command line: migrate, regenerate, and verify the schema module."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def _run(*arguments: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "relq_migrate", *map(str, arguments)],
        capture_output=True,
        check=False,
        text=True,
    )


def _folder(tmp_path: Path, **files: str) -> Path:
    folder = tmp_path / "migrations"
    folder.mkdir(exist_ok=True)
    for name, sql in files.items():
        (folder / f"{name}.sql").write_text(sql, encoding="utf-8")
    return folder


_ACCOUNTS = "create table accounts (id integer primary key, name text not null);"


def test_migrates_without_codegen(tmp_path: Path) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})

    result = _run("sqlite", tmp_path / "app.db", folder)

    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert (tmp_path / "app.db").exists()


def test_codegen_follows_the_migration_and_leaves_out_the_history_table(tmp_path: Path) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})
    output = tmp_path / "schema.py"

    result = _run("sqlite", tmp_path / "app.db", folder, "--codegen", output)

    assert result.returncode == 0, result.stderr
    generated = output.read_text()
    assert "class Accounts(Table" in generated
    assert "relq_migrations" not in generated


def test_codegen_leaves_out_a_custom_history_table(tmp_path: Path) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})
    output = tmp_path / "schema.py"

    result = _run("sqlite", tmp_path / "app.db", folder, "--table", "history", "--codegen", output)

    assert result.returncode == 0, result.stderr
    assert "history" not in output.read_text()


def test_check_passes_when_current_and_fails_when_the_module_is_stale(tmp_path: Path) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})
    database, output = tmp_path / "app.db", tmp_path / "schema.py"
    assert _run("sqlite", database, folder, "--codegen", output).returncode == 0
    committed = output.read_text()

    assert _run("sqlite", database, folder, "--codegen", output, "--check").returncode == 0

    output.write_text(committed.replace("name", "title"))
    stale = _run("sqlite", database, folder, "--codegen", output, "--check")
    assert stale.returncode == 1
    assert stale.stderr.splitlines() == [
        f"relq-migrate: codegen: generated schema is stale: {output}"
    ]
    assert "title" in output.read_text()

    assert _run("sqlite", database, folder, "--codegen", output).returncode == 0
    assert output.read_text() == committed


def test_check_does_not_apply_pending_migrations(tmp_path: Path) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})
    database, output = tmp_path / "app.db", tmp_path / "schema.py"
    assert _run("sqlite", database, folder, "--codegen", output).returncode == 0
    committed = output.read_text()
    _folder(tmp_path, **{"0002_email": "alter table accounts add column email text;"})
    bytes_before = database.read_bytes()

    pending = _run("sqlite", database, folder, "--codegen", output, "--check")

    assert pending.returncode == 1
    assert pending.stderr.splitlines() == [
        "relq-migrate: 1 migration(s) pending (0002_email.sql); run without --check to apply them"
    ]
    assert database.read_bytes() == bytes_before
    assert output.read_text() == committed

    assert _run("sqlite", database, folder, "--codegen", output).returncode == 0
    assert "email" in output.read_text()
    assert _run("sqlite", database, folder, "--codegen", output, "--check").returncode == 0


def test_check_does_not_create_a_missing_database(tmp_path: Path) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})
    database = tmp_path / "absent.db"

    result = _run("sqlite", database, folder, "--codegen", tmp_path / "schema.py", "--check")

    assert result.returncode == 1
    assert result.stderr.startswith(f"relq-migrate: cannot open {database}")
    assert not database.exists()


def test_check_on_a_database_that_was_never_migrated_reports_everything_pending(
    tmp_path: Path,
) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})
    database = tmp_path / "empty.db"
    sqlite3.connect(database).close()

    result = _run("sqlite", database, folder, "--codegen", tmp_path / "schema.py", "--check")

    assert result.returncode == 1
    assert "1 migration(s) pending (0001_accounts.sql)" in result.stderr
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("select count(*) from sqlite_master").fetchall() == [(0,)]
    finally:
        connection.close()


def test_config_maps_types_the_database_has(tmp_path: Path) -> None:
    folder = _folder(tmp_path, **{"0001_prices": "create table prices (amount money not null);"})
    config = tmp_path / "relq-codegen.toml"
    config.write_text('[types.money]\npython = "int"\ndecoder = "int"\n')
    output = tmp_path / "schema.py"

    unmapped = _run("sqlite", tmp_path / "a.db", folder, "--codegen", output)
    assert unmapped.returncode == 1
    assert unmapped.stderr.startswith("relq-migrate: codegen: unsupported database type")
    assert len(unmapped.stderr.splitlines()) == 1

    mapped = _run("sqlite", tmp_path / "b.db", folder, "--codegen", output, "--config", config)
    assert mapped.returncode == 0, mapped.stderr
    assert "amount: Column[int] = column()" in output.read_text()


def test_a_failing_migration_is_one_line_and_skips_codegen(tmp_path: Path) -> None:
    folder = _folder(tmp_path, **{"0001_bad": "create tabel accounts (id integer);"})
    output = tmp_path / "schema.py"

    result = _run("sqlite", tmp_path / "app.db", folder, "--codegen", output)

    assert result.returncode == 1
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("relq-migrate: migration '0001_bad.sql' failed: ")
    assert not output.exists()


def test_missing_migration_folder_is_one_line(tmp_path: Path) -> None:
    result = _run("sqlite", tmp_path / "app.db", tmp_path / "missing")

    assert result.returncode == 1
    assert result.stderr.splitlines() == [
        f"relq-migrate: migration folder does not exist: {tmp_path / 'missing'}"
    ]


def test_busy_timeout_is_validated(tmp_path: Path) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})

    result = _run("sqlite", tmp_path / "app.db", folder, "--busy-timeout", "-1")

    assert result.returncode == 1
    assert result.stderr.splitlines() == [
        "relq-migrate: busy_timeout must be a non-negative number of seconds"
    ]


@pytest.mark.parametrize("option", [["--check"], ["--config", "x.toml"]])
def test_check_and_config_need_codegen(tmp_path: Path, option: list[str]) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})

    result = _run("sqlite", tmp_path / "app.db", folder, *option)

    assert result.returncode == 2
    assert "need --codegen" in result.stderr
