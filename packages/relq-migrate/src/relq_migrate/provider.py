"""Filesystem migration discovery."""

from __future__ import annotations

import re
from pathlib import Path

from ._model import Migration, MigrationError

_FILENAME_RE = re.compile(r"^(?P<version>[0-9]{4})_(?P<label>[a-z0-9_]+)\.sql$")
_DIRECTIVE_RE = re.compile(r"--\s*relq:(?P<body>.*)", re.IGNORECASE)
_FOREIGN_KEYS_OFF_RE = re.compile(r"\s*foreign_keys\s*=\s*off\s*", re.IGNORECASE)


class FileMigrationProvider:
    """Load contiguous, ordered ``0001_name.sql`` files from a folder.

    Non-SQL files are ignored, so README files and editor metadata can live
    beside migrations. SQL files must match the migration filename convention.
    The matching files must start at ``0001`` and have no gaps or duplicate
    numeric versions.

    A file whose leading comments include ``-- relq: foreign_keys = off`` is
    applied with foreign-key enforcement off, which table rebuilds need
    (``SQLiteMigrator`` only).
    """

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def migrations(self) -> tuple[Migration, ...]:
        if not self._folder.is_dir():
            raise NotADirectoryError(f"migration folder does not exist: {self._folder}")

        found: list[tuple[int, Path]] = []
        invalid_sql: list[str] = []
        for entry in self._folder.iterdir():
            if not entry.is_file():
                continue
            match = _FILENAME_RE.fullmatch(entry.name)
            if match is None:
                if entry.suffix.lower() == ".sql":
                    invalid_sql.append(entry.name)
                continue
            found.append((int(match.group("version")), entry))

        if invalid_sql:
            names = ", ".join(sorted(invalid_sql))
            raise MigrationError(
                f"invalid migration filename(s): {names}; expected NNNN_lower_case.sql"
            )

        found.sort(key=lambda item: item[0])
        versions = [version for version, _ in found]
        if len(set(versions)) != len(versions):
            raise MigrationError(f"duplicate migration versions: {versions}")
        expected = list(range(1, len(versions) + 1))
        if versions != expected:
            raise MigrationError(f"migration versions must be contiguous from 1: {versions}")

        return tuple(_read_migration(path) for _, path in found)


def _read_migration(path: Path) -> Migration:
    sql = path.read_text(encoding="utf-8")
    return Migration(path.name, sql, foreign_keys=_header_foreign_keys(path.name, sql))


def _header_foreign_keys(name: str, sql: str) -> bool:
    """Read ``-- relq: key = value`` directives from the file's leading comments."""
    foreign_keys = True
    for line in sql.splitlines():
        text = line.strip()
        if not text:
            continue
        if not text.startswith("--"):
            break
        directive = _DIRECTIVE_RE.fullmatch(text)
        if directive is None:
            continue
        if _FOREIGN_KEYS_OFF_RE.fullmatch(directive.group("body")) is None:
            raise MigrationError(
                f"migration {name!r} has an unsupported directive {text!r}; "
                "the only directive is '-- relq: foreign_keys = off'"
            )
        foreign_keys = False
    return foreign_keys
