"""Fixed-dialect compilation entry points."""

from relq._compiler._model import CompiledQuery, Dialect
from relq._compiler._render import render_query
from relq._compiler.validation import validate_query
from relq._query import Query, extract_query

_SQLITE = Dialect("sqlite", "?", max_parameters=999)
_POSTGRES = Dialect(
    "postgres",
    "$",
    supports_row_locking=True,
    supports_temporal_arithmetic=True,
    max_parameters=65_535,
)


def compile_sqlite[Row](query: Query[Row]) -> CompiledQuery:
    return _compile(query, _SQLITE)


def compile_postgres[Row](query: Query[Row]) -> CompiledQuery:
    return _compile(query, _POSTGRES)


def _compile[Row](query: Query[Row], dialect: Dialect) -> CompiledQuery:
    """Run relq's complete AST-only compilation pipeline for one fixed dialect."""
    node = extract_query(query).node
    validate_query(node, dialect)
    return render_query(node, dialect)
