"""Forward-only schema migrations for relq applications."""

from typing import TYPE_CHECKING

from ._model import (
    Migration,
    MigrationError,
    MigrationReport,
    MigrationResult,
    MigrationStatus,
)
from .provider import FileMigrationProvider
from .sqlite import SQLiteMigrator

if TYPE_CHECKING:
    from .postgres import PostgresMigrator as _PostgresMigrator

    PostgresMigrator = _PostgresMigrator

__all__ = [
    "FileMigrationProvider",
    "Migration",
    "MigrationError",
    "MigrationReport",
    "MigrationResult",
    "MigrationStatus",
    "SQLiteMigrator",
]


def __getattr__(name: str) -> object:
    """Load the PostgreSQL adapter only when it is requested."""
    if name != "PostgresMigrator":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        from .postgres import PostgresMigrator
    except ModuleNotFoundError as error:
        if error.name == "asyncpg":
            raise ImportError(
                "PostgresMigrator requires the optional relq-migrate[postgres] extra"
            ) from error
        raise
    return PostgresMigrator
