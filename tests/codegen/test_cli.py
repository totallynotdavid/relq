"""Code-generation CLI contracts, run as the installed command would run."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch
from relq_codegen.__main__ import main as codegen_main


def _codegen(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "relq_codegen", *arguments],
        capture_output=True,
        check=False,
        cwd=cwd,
        text=True,
    )


def _database(tmp_path: Path, ddl: str) -> Path:
    path = tmp_path / "schema.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(ddl)
    connection.close()
    return path


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


def test_an_unmapped_type_is_one_line_and_exit_code_one(tmp_path: Path) -> None:
    database = _database(tmp_path, "create table places (id integer primary key, area geometry)")

    result = _codegen("sqlite", str(database), str(tmp_path / "out.py"))

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        "relq-codegen: unsupported database type 'geometry'; map it in a relq-codegen --config file"
    ]
    assert not (tmp_path / "out.py").exists()


def test_a_toml_config_maps_the_type_the_cli_rejected(tmp_path: Path) -> None:
    database = _database(tmp_path, "create table places (id integer primary key, area geometry)")
    config = tmp_path / "relq-codegen.toml"
    config.write_text('[types.geometry]\npython = "str"\ndecoder = "str"\n')
    output = tmp_path / "out.py"

    result = _codegen("sqlite", str(database), str(output), "--config", str(config))

    assert result.returncode == 0, result.stderr
    assert "area: Column[str | None] = column()" in output.read_text()
    namespace: dict[str, object] = {}
    exec(output.read_text(), namespace)  # noqa: S102 - generated source is the subject under test.


def test_a_toml_config_declares_qualified_python_types_and_wrappers(tmp_path: Path) -> None:
    database = _database(
        tmp_path,
        "create table places (id integer primary key, area geometry not null, cost money)",
    )
    config = tmp_path / "relq-codegen.toml"
    config.write_text(
        '[types.geometry]\npython = "list[decimal.Decimal] | str"\ndecoder = "str"\n'
        '[types.money]\nwrapper = "Cents"\npython = "int"\ndecoder = "int"\n'
    )
    output = tmp_path / "out.py"

    result = _codegen("sqlite", str(database), str(output), "--config", str(config))

    assert result.returncode == 0, result.stderr
    generated = output.read_text()
    assert "import decimal" in generated
    assert "area: Column[list[decimal.Decimal] | str] = column()" in generated
    assert "Cents = NewType('Cents', int)" in generated
    assert "cost: Column[Cents | None] = column()" in generated


def test_a_toml_config_can_reject_a_type_with_its_reason(tmp_path: Path) -> None:
    database = _database(tmp_path, "create table places (id integer primary key, area geometry)")
    config = tmp_path / "relq-codegen.toml"
    config.write_text('[types.geometry]\nreject = "no value object yet"\n')

    result = _codegen("sqlite", str(database), str(tmp_path / "out.py"), "--config", str(config))

    assert result.returncode == 1
    assert result.stderr.splitlines() == [
        "relq-codegen: database type 'geometry' is rejected: no value object yet"
    ]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[types.geometry\n", "invalid config"),
        ('[typos.geometry]\npython = "str"\n', "unknown table 'typos'"),
        ('[types.geometry]\npython = "str"\n', "needs string 'python' and 'decoder'"),
        ('[types.geometry]\npython = "str"\ndecoder = "text"\n', "decoder 'text' is not one of"),
        ('[types.geometry]\npython = "Decimal"\ndecoder = "str"\n', "write 'module.Decimal'"),
        ('[types.geometry]\npython = "str("\ndecoder = "str"\n', "not a Python expression"),
        ('[types.geometry]\npython = "str"\ndecoder = "str"\nextra = 1\n', "unknown key 'extra'"),
        ('[types.geometry]\nreject = "no"\npython = "str"\n', "'reject' takes a reason"),
    ],
)
def test_an_invalid_toml_config_is_a_one_line_error(
    tmp_path: Path, content: str, message: str
) -> None:
    database = _database(tmp_path, "create table places (id integer primary key, area geometry)")
    config = tmp_path / "relq-codegen.toml"
    config.write_text(content)

    result = _codegen("sqlite", str(database), str(tmp_path / "out.py"), "--config", str(config))

    assert result.returncode == 1
    assert len(result.stderr.splitlines()) == 1
    assert message in result.stderr


def test_a_missing_config_file_is_a_one_line_error(tmp_path: Path) -> None:
    database = _database(tmp_path, "create table places (id integer primary key)")

    result = _codegen(
        "sqlite", str(database), str(tmp_path / "out.py"), "--config", str(tmp_path / "no.toml")
    )

    assert result.returncode == 1
    assert result.stderr.startswith("relq-codegen: cannot read config ")
    assert len(result.stderr.splitlines()) == 1


def test_a_python_module_config_is_found_from_the_working_directory(tmp_path: Path) -> None:
    database = _database(tmp_path, "create table places (id integer primary key, area geometry)")
    (tmp_path / "codegen_types.py").write_text(
        "from relq_codegen import CodegenConfig, DirectType, Name, TypeIdentity\n"
        "CONFIG = CodegenConfig({TypeIdentity(None, 'geometry'): DirectType(Name('bytes'), 'bytes')})\n"
        "OTHER = CodegenConfig({TypeIdentity(None, 'geometry'): DirectType(Name('str'), 'str')})\n"
    )
    output = tmp_path / "out.py"

    default = _codegen(
        "sqlite", str(database), str(output), "--config", "codegen_types", cwd=tmp_path
    )
    assert default.returncode == 0, default.stderr
    assert "area: Column[bytes | None] = column()" in output.read_text()

    named = _codegen(
        "sqlite", str(database), str(output), "--config", "codegen_types:OTHER", cwd=tmp_path
    )
    assert named.returncode == 0, named.stderr
    assert "area: Column[str | None] = column()" in output.read_text()


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("no_such_module", "cannot import config module 'no_such_module'"),
        ("codegen_types:MISSING", "must define MISSING as a CodegenConfig"),
    ],
)
def test_a_bad_python_module_config_is_a_one_line_error(
    tmp_path: Path, reference: str, message: str
) -> None:
    database = _database(tmp_path, "create table places (id integer primary key)")
    (tmp_path / "codegen_types.py").write_text("CONFIG = 1\n")

    result = _codegen(
        "sqlite", str(database), str(tmp_path / "out.py"), "--config", reference, cwd=tmp_path
    )

    assert result.returncode == 1
    assert len(result.stderr.splitlines()) == 1
    assert message in result.stderr


def test_a_formatted_module_stays_fresh_and_is_not_rewritten(tmp_path: Path) -> None:
    """The formatter owns the layout; --check and regeneration compare syntax."""
    database = _database(
        tmp_path, "create table notes (id integer primary key, body text not null, meta json)"
    )
    output = tmp_path / "out.py"
    assert _codegen("sqlite", str(database), str(output)).returncode == 0
    raw = output.read_text()

    formatted = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--isolated", str(output)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert formatted.returncode == 0, formatted.stderr
    assert output.read_text() != raw, "the renderer's layout is not the formatter's layout"
    formatted_text = output.read_text()

    assert _codegen("sqlite", str(database), str(output), "--check").returncode == 0
    assert _codegen("sqlite", str(database), str(output)).returncode == 0
    assert output.read_text() == formatted_text


def test_check_still_fails_when_a_formatted_module_drifts_in_meaning(tmp_path: Path) -> None:
    database = _database(tmp_path, "create table notes (id integer primary key, body text)")
    output = tmp_path / "out.py"
    assert _codegen("sqlite", str(database), str(output)).returncode == 0
    output.write_text(output.read_text().replace("body: Column[str | None]", "body: Column[str]"))

    result = _codegen("sqlite", str(database), str(output), "--check")

    assert result.returncode == 1
    assert result.stderr.startswith("relq-codegen: generated schema is stale: ")


def test_check_fails_for_a_missing_module(tmp_path: Path) -> None:
    database = _database(tmp_path, "create table notes (id integer primary key)")

    result = _codegen("sqlite", str(database), str(tmp_path / "absent.py"), "--check")

    assert result.returncode == 1
    assert "stale" in result.stderr
