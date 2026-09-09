"""Shared migration data types and validation helpers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MigrationError(RuntimeError):
    """The migration history or migration file set is invalid."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One forward-only SQL migration file."""

    name: str
    sql: str


class MigrationStatus(StrEnum):
    """The outcome of one migration attempt, matching Kysely's statuses."""

    SUCCESS = "Success"
    ERROR = "Error"
    NOT_EXECUTED = "NotExecuted"


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """The outcome for one migration considered by a migration run."""

    migration_name: str
    status: MigrationStatus
    error: BaseException | None = None

    @property
    def name(self) -> str:
        """A short Python-friendly alias for ``migration_name``."""
        return self.migration_name


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """The result of migrating one database to the provider's latest file."""

    error: BaseException | None
    results: tuple[MigrationResult, ...]

    @property
    def ok(self) -> bool:
        """Whether the run completed without an error."""
        return self.error is None


def migration_checksum(migration: Migration) -> str:
    """Return the stable checksum stored with an applied migration."""
    return hashlib.sha256(migration.sql.encode("utf-8")).hexdigest()


def validate_identifier(identifier: str, *, kind: str) -> str:
    """Validate an identifier before it is inserted into DDL or SQL text."""
    if _IDENTIFIER_RE.fullmatch(identifier) is None:
        raise ValueError(f"invalid {kind} identifier: {identifier!r}")
    return identifier


def quote_identifier(identifier: str) -> str:
    """Quote an identifier that has already passed ``validate_identifier``."""
    return f'"{identifier}"'


def pending_migrations(
    migrations: tuple[Migration, ...], applied: Mapping[str, str]
) -> tuple[Migration, ...]:
    """Verify history integrity and return the unapplied suffix."""
    by_name = {migration.name: migration for migration in migrations}
    unknown = sorted(set(applied).difference(by_name))
    if unknown:
        names = ", ".join(unknown)
        raise MigrationError(
            f"the database contains migration(s) not shipped by this package: {names}; "
            "migrations are forward-only"
        )

    for migration in migrations:
        recorded_checksum = applied.get(migration.name)
        if recorded_checksum is None:
            continue
        checksum = migration_checksum(migration)
        if recorded_checksum != checksum:
            raise MigrationError(
                f"migration {migration.name!r} was modified after it was applied "
                f"(recorded checksum {recorded_checksum[:12]}, packaged {checksum[:12]}); "
                "write a new migration instead"
            )

    first_pending = len(migrations)
    for index, migration in enumerate(migrations):
        if migration.name not in applied:
            first_pending = index
            break
    later_applied = {
        migration.name for migration in migrations[first_pending:] if migration.name in applied
    }
    if later_applied:
        names = ", ".join(sorted(later_applied))
        raise MigrationError(
            f"migration history is not an ordered prefix; later migration(s) are applied: {names}"
        )
    return migrations[first_pending:]
