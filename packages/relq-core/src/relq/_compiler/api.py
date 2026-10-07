"""Fixed-dialect compilation entry points."""

from relq._compiler._model import CompiledQuery, Dialect
from relq._compiler._render import render_query
from relq._compiler.validation import validate_query
from relq._query import Query, extract_query

SQLITE_MAX_PARAMETERS = 999
# PostgreSQL's protocol allows 65,535 bind parameters, but asyncpg, the only
# supported driver, rejects a statement with more than 32,767.
POSTGRES_MAX_PARAMETERS = 32_767

_SQLITE = Dialect("sqlite", "?", max_parameters=SQLITE_MAX_PARAMETERS)
_POSTGRES = Dialect(
    "postgres",
    "$",
    supports_row_locking=True,
    supports_temporal_arithmetic=True,
    supports_conflict_predicates=True,
    max_parameters=POSTGRES_MAX_PARAMETERS,
    supports_schema_qualified_tables=True,
    supports_data_modifying_ctes=True,
    supports_postgres_expressions=True,
)


def compile_sqlite[Row](query: Query[Row]) -> CompiledQuery:
    return _compile(query, _SQLITE)


def compile_postgres[Row](query: Query[Row]) -> CompiledQuery:
    return _compile(query, _POSTGRES)


def _compile[Row](query: Query[Row], dialect: Dialect) -> CompiledQuery:
    node = extract_query(query).node
    validate_query(node, dialect)
    return render_query(node, dialect)
