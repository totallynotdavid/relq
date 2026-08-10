"""Exact SQL contracts shared by compiler feature tests."""

from relq._compiler import CompiledQuery, compile_postgres, compile_sqlite
from relq.query import SelectQuery


def assert_compiles[Row](
    query: SelectQuery[Row],
    *,
    sqlite: CompiledQuery,
    postgres: CompiledQuery,
) -> None:
    """Assert both fixed compiler flavors, including parameter ordering."""
    assert compile_sqlite(query) == sqlite
    assert compile_postgres(query) == postgres
