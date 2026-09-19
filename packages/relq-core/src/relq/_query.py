"""Private immutable state shared by concrete public query values."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, overload

from relq._ast import QueryNode, SelectNode
from relq.rows import RowAdapter

if TYPE_CHECKING:
    from typing import Literal

    from relq.dml import DeleteQuery, InsertQuery, UpdateQuery
    from relq.query import SelectQuery


@dataclass(frozen=True, slots=True)
class _QueryState[Row]:
    """The sole internal representation consumed outside query builders."""

    node: QueryNode
    adapter: RowAdapter[Row] | None = None


@dataclass(frozen=True, slots=True, init=False)
class Query[Row]:
    """Base for concrete public queries; direct construction is prohibited."""

    __query_state: _QueryState[Row]

    def __init__(self) -> None:
        raise TypeError("query values are created by relq builders, not constructors")


@overload
def new_query[SqlRow, Row](
    query_type: type[SelectQuery[SqlRow, Row]],
    node: QueryNode,
    adapter: RowAdapter[Row] | None = None,
    *,
    table: object | None = None,
) -> SelectQuery[SqlRow, Row]: ...


@overload
def new_query[Row, Returns: (Literal[True], Literal[False])](
    query_type: type[InsertQuery[Row, Returns]],
    node: QueryNode,
    adapter: RowAdapter[Row] | None = None,
    *,
    table: object | None = None,
) -> InsertQuery[Row, Returns]: ...


@overload
def new_query[
    Row,
    Returns: (Literal[True], Literal[False]),
    Bounded: (Literal[True], Literal[False]),
](
    query_type: type[UpdateQuery[Row, Returns, Bounded]],
    node: QueryNode,
    adapter: RowAdapter[Row] | None = None,
    *,
    table: object | None = None,
) -> UpdateQuery[Row, Returns, Bounded]: ...


@overload
def new_query[
    Row,
    Returns: (Literal[True], Literal[False]),
    Bounded: (Literal[True], Literal[False]),
](
    query_type: type[DeleteQuery[Row, Returns, Bounded]],
    node: QueryNode,
    adapter: RowAdapter[Row] | None = None,
    *,
    table: object | None = None,
) -> DeleteQuery[Row, Returns, Bounded]: ...


@overload
def new_query[Q, Row](
    query_type: type[Q],
    node: QueryNode,
    adapter: RowAdapter[Row] | None = None,
    *,
    table: object | None = None,
) -> Q: ...


def new_query[Q, Row](
    query_type: type[Q],
    node: QueryNode,
    adapter: RowAdapter[Row] | None = None,
    *,
    table: object | None = None,
) -> Q:
    """Create a frozen concrete query without exposing AST construction publicly."""
    query = object.__new__(query_type)
    object.__setattr__(query, "_Query__query_state", _QueryState(node, adapter))
    if table is not None:
        object.__setattr__(query, "_table", table)
    return query


def extract_query[Row](query: Query[Row]) -> _QueryState[Row]:
    """Return private query state for builders, compilers, and executors."""
    return cast(_QueryState[Row], object.__getattribute__(query, "_Query__query_state"))


def select_node[Row](query: Query[Row]) -> SelectNode:
    """Return a SELECT node for builder implementation code."""
    node = extract_query(query).node
    if not isinstance(node, SelectNode):  # pragma: no cover - builder invariant
        raise TypeError("expected a SELECT query")
    return node
