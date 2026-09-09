"""Unit contracts for migration file discovery and report data."""

from pathlib import Path

import pytest
from relq_migrate import (
    FileMigrationProvider,
    Migration,
    MigrationError,
    MigrationResult,
    MigrationStatus,
)


def _write(folder: Path, name: str, sql: str) -> None:
    (folder / name).write_text(sql, encoding="utf-8")


def test_provider_orders_numbered_files_and_ignores_other_files(tmp_path: Path) -> None:
    _write(tmp_path, "0002_add_status.sql", "alter table users add column status text;")
    _write(tmp_path, "0001_create_users.sql", "create table users (id integer);")
    _write(tmp_path, "README.md", "Migration notes")
    _write(tmp_path, "local.sql", "not a migration")

    assert FileMigrationProvider(tmp_path).migrations() == (
        Migration("0001_create_users.sql", "create table users (id integer);"),
        Migration("0002_add_status.sql", "alter table users add column status text;"),
    )


def test_provider_rejects_gaps_and_duplicate_versions(tmp_path: Path) -> None:
    _write(tmp_path, "0001_create_users.sql", "create table users (id integer);")
    _write(tmp_path, "0003_add_status.sql", "alter table users add column status text;")
    with pytest.raises(MigrationError, match="contiguous"):
        FileMigrationProvider(tmp_path).migrations()

    (tmp_path / "0003_add_status.sql").unlink()
    _write(tmp_path, "0001_other_name.sql", "select 1;")
    with pytest.raises(MigrationError, match="duplicate"):
        FileMigrationProvider(tmp_path).migrations()


def test_provider_requires_a_directory(tmp_path: Path) -> None:
    path = tmp_path / "missing"
    with pytest.raises(NotADirectoryError):
        FileMigrationProvider(path).migrations()


def test_report_result_exposes_kysely_status_names() -> None:
    result = MigrationResult("0001_create_users.sql", MigrationStatus.SUCCESS)
    assert result.name == result.migration_name
    assert result.status == "Success"
