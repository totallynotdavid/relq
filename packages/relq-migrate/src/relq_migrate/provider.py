"""Filesystem migration discovery."""

from __future__ import annotations

import re
from pathlib import Path

from ._model import Migration, MigrationError

_FILENAME_RE = re.compile(r"^(?P<version>[0-9]{4})_(?P<label>[a-z0-9_]+)\.sql$")


class FileMigrationProvider:
    """Load contiguous, ordered ``0001_name.sql`` files from a folder.

    Files that do not match the migration filename convention are ignored so
    README files and editor metadata can live beside migrations. Matching files
    must start at ``0001`` and have no gaps or duplicate numeric versions.
    """

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def migrations(self) -> tuple[Migration, ...]:
        if not self._folder.is_dir():
            raise NotADirectoryError(f"migration folder does not exist: {self._folder}")

        found: list[tuple[int, Path]] = []
        for entry in self._folder.iterdir():
            if not entry.is_file():
                continue
            match = _FILENAME_RE.fullmatch(entry.name)
            if match is None:
                continue
            found.append((int(match.group("version")), entry))

        found.sort(key=lambda item: item[0])
        versions = [version for version, _ in found]
        if len(set(versions)) != len(versions):
            raise MigrationError(f"duplicate migration versions: {versions}")
        expected = list(range(1, len(versions) + 1))
        if versions != expected:
            raise MigrationError(f"migration versions must be contiguous from 1: {versions}")

        return tuple(Migration(path.name, path.read_text(encoding="utf-8")) for _, path in found)
