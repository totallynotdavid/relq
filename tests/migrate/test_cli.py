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


def _migrated(tmp_path: Path) -> tuple[Path, Path]:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})
    database = tmp_path / "app.db"
    assert _run("sqlite", database, folder).returncode == 0
    return folder, database


def _assert_one_line(result: subprocess.CompletedProcess[str], message: str) -> None:
    assert result.returncode == 1
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith(f"relq-migrate: {message}"), lines[0]


@pytest.mark.parametrize("check", [False, True])
def test_an_edited_applied_migration_is_one_line(tmp_path: Path, check: bool) -> None:
    folder, database = _migrated(tmp_path)
    (folder / "0001_accounts.sql").write_text("create table accounts (id integer);")
    codegen = ["--codegen", str(tmp_path / "schema.py")]

    result = _run("sqlite", database, folder, *codegen, *(["--check"] if check else []))

    _assert_one_line(result, "migration '0001_accounts.sql' was modified after it was applied")
    assert not (tmp_path / "schema.py").exists()


@pytest.mark.parametrize("check", [False, True])
def test_a_database_with_a_migration_the_folder_lacks_is_one_line(
    tmp_path: Path, check: bool
) -> None:
    folder, database = _migrated(tmp_path)
    (folder / "0001_accounts.sql").unlink()
    (folder / "0001_other.sql").write_text(_ACCOUNTS)
    codegen = ["--codegen", str(tmp_path / "schema.py")]

    result = _run("sqlite", database, folder, *codegen, *(["--check"] if check else []))

    _assert_one_line(result, "the database contains migration(s) not shipped by this package")


@pytest.mark.parametrize("check", [False, True])
def test_an_invalid_migration_filename_is_one_line(tmp_path: Path, check: bool) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS, "Bad": _ACCOUNTS})
    database = tmp_path / "app.db"
    sqlite3.connect(database).close()
    codegen = ["--codegen", str(tmp_path / "schema.py")]

    result = _run("sqlite", database, folder, *codegen, *(["--check"] if check else []))

    _assert_one_line(result, "invalid migration filename(s): Bad.sql")


@pytest.mark.parametrize("check", [False, True])
def test_a_migration_file_that_is_not_utf8_is_one_line(tmp_path: Path, check: bool) -> None:
    folder = _folder(tmp_path)
    (folder / "0001_accounts.sql").write_bytes(b"\xff\xfe")
    database = tmp_path / "app.db"
    sqlite3.connect(database).close()
    codegen = ["--codegen", str(tmp_path / "schema.py")]

    result = _run("sqlite", database, folder, *codegen, *(["--check"] if check else []))

    _assert_one_line(result, "'utf-8' codec can't decode")


@pytest.mark.parametrize("check", [False, True])
def test_a_database_file_that_is_not_sqlite_is_one_line(tmp_path: Path, check: bool) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})
    database = tmp_path / "app.db"
    database.write_text("this is not a database file" * 10)
    codegen = ["--codegen", str(tmp_path / "schema.py")]

    result = _run("sqlite", database, folder, *codegen, *(["--check"] if check else []))

    _assert_one_line(result, "file is not a database")


def test_an_output_path_that_cannot_be_written_is_one_line(tmp_path: Path) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a folder is needed")

    result = _run("sqlite", tmp_path / "app.db", folder, "--codegen", blocker / "schema.py")

    _assert_one_line(result, "[Errno ")


MALFORMED_DSNS = [
    "not-a-dsn",
    "notadsn://x",
    "postgresql://relq@127.0.0.1:notaport/db",
    "postgresql://relq@127.0.0.1/db?sslmode=bogus",
]


@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("dsn", MALFORMED_DSNS)
def test_a_malformed_postgres_dsn_is_one_line(tmp_path: Path, dsn: str, check: bool) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})
    codegen = ["--codegen", str(tmp_path / "schema.py")]

    result = _run("postgres", dsn, folder, *codegen, *(["--check"] if check else []))

    _assert_one_line(result, "cannot connect to PostgreSQL: ")
    assert not (tmp_path / "schema.py").exists()


@pytest.mark.parametrize("reference", ["", ":CONFIG", ".relative"])
def test_a_malformed_python_config_reference_is_one_line(tmp_path: Path, reference: str) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})

    result = _run(
        "sqlite",
        tmp_path / "app.db",
        folder,
        "--codegen",
        tmp_path / "schema.py",
        "--config",
        reference,
    )

    _assert_one_line(result, "codegen: config ")


def test_a_config_file_that_is_not_utf8_is_one_line(tmp_path: Path) -> None:
    folder = _folder(tmp_path, **{"0001_accounts": _ACCOUNTS})
    config = tmp_path / "types.toml"
    config.write_bytes(b"\xff\xfe")

    result = _run(
        "sqlite",
        tmp_path / "app.db",
        folder,
        "--codegen",
        tmp_path / "schema.py",
        "--config",
        config,
    )

    _assert_one_line(result, f"codegen: invalid config {config}: 'utf-8' codec can't decode")


@pytest.mark.parametrize("check", [False, True])
def test_an_existing_output_that_is_not_utf8_is_stale_not_a_crash(
    tmp_path: Path, check: bool
) -> None:
    folder, database = _migrated(tmp_path)
    output = tmp_path / "schema.py"
    output.write_bytes(b"\xff\xfe")

    result = _run("sqlite", database, folder, "--codegen", output, *(["--check"] if check else []))

    if check:
        _assert_one_line(result, "codegen: generated schema is stale")
    else:
        assert (result.returncode, result.stderr) == (0, "")
        assert "class Accounts(Table" in output.read_text()
