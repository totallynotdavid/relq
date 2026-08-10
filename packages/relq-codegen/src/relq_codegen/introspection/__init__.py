"""Database-specific catalog adapters."""

from relq_codegen.introspection.postgres import inspect_postgres, inspect_postgres_enums
from relq_codegen.introspection.sqlite import inspect_sqlite

__all__ = ["inspect_postgres", "inspect_postgres_enums", "inspect_sqlite"]
