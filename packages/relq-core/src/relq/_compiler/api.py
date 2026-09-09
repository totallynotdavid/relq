"""Fixed-dialect compilation entry points."""

from typing import Literal

from relq._compiler._model import CompiledQuery, Dialect
from relq._compiler._render import render_query
from relq._compiler.validation import validate_query
from relq._query import Query, extract_query
from relq.dml import (
    DeleteQuery,
    InsertQuery,
    ModelDeleteQuery,
    ModelInsertQuery,
    ModelUpdateQuery,
    UpdateQuery,
)
from relq.query import ModelSelectQuery, SelectQuery

_SQLITE = Dialect("sqlite", "?", max_parameters=999)
_POSTGRES = Dialect(
    "postgres",
    "$",
    max_parameters=65_535,
    supports_schema_qualified_tables=True,
    supports_data_modifying_ctes=True,
    supports_postgres_expressions=True,
)


type CompilableQuery[
    Row,
    Returns: (Literal[True], Literal[False]),
    Bounded: (Literal[True], Literal[False]),
] = (
    SelectQuery[Row]
    | ModelSelectQuery[Row]
    | InsertQuery[Row, Returns]
    | ModelInsertQuery[Row]
    | UpdateQuery[Row, Returns, Bounded]
    | ModelUpdateQuery[Row]
    | DeleteQuery[Row, Returns, Bounded]
    | ModelDeleteQuery[Row]
)


def compile_sqlite[
    Row,
    Returns: (Literal[True], Literal[False]),
    Bounded: (Literal[True], Literal[False]),
](query: CompilableQuery[Row, Returns, Bounded]) -> CompiledQuery:
    return _compile(query, _SQLITE)


def compile_postgres[
    Row,
    Returns: (Literal[True], Literal[False]),
    Bounded: (Literal[True], Literal[False]),
](query: CompilableQuery[Row, Returns, Bounded]) -> CompiledQuery:
    return _compile(query, _POSTGRES)


def _compile[Row](query: Query[Row], dialect: Dialect) -> CompiledQuery:
    """Run relq's complete AST-only compilation pipeline for one fixed dialect."""
    node = extract_query(query).node
    validate_query(node)
    return render_query(node, dialect)
