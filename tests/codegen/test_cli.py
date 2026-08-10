"""Code-generation CLI freshness contract, independent of catalog flavor."""

import sqlite3
import sys
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch
from relq_codegen.__main__ import main as codegen_main


def test_sqlite_codegen_cli_check_detects_schema_drift(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    database_path = tmp_path / "schema.sqlite"
    output = tmp_path / "db_schema.py"
    connection = sqlite3.connect(database_path)
    connection.execute("create table users (id integer primary key)")
    connection.close()
    monkeypatch.setattr(sys, "argv", ["relq-codegen", "sqlite", str(database_path), str(output)])
    codegen_main()
    monkeypatch.setattr(
        sys, "argv", ["relq-codegen", "sqlite", str(database_path), str(output), "--check"]
    )
    codegen_main()
    connection = sqlite3.connect(database_path)
    connection.execute("alter table users add column email text")
    connection.close()
    with pytest.raises(SystemExit, match="stale"):
        codegen_main()
