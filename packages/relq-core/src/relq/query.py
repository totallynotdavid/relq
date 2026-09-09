"""Immutable typed SELECT builder and relational composition."""

from dataclasses import dataclass, replace
from typing import Generic, Self, TypeVar, overload

from relq._ast import (
    AliasNode,
    BinaryNode,
    ColumnNode,
    CompoundNode,
    CompoundOperator,
    CteNode,
    DerivedSourceNode,
    JoinNode,
    Node,
    NullableResultNode,
    QueryNode,
    SelectNode,
    StarNode,
)
from relq._query import Query, extract_query, new_query, select_node
from relq.expressions import (
    BooleanExpression,
    CteTable,
    DerivedTable,
    Expr,
    Expression,
    Order,
    Source,
)
from relq.rows import RowAdapter, row_adapter

Row_co = TypeVar("Row_co", covariant=True)


@dataclass(frozen=True, slots=True, init=False)
class _SelectQuery(Query[Row_co], Generic[Row_co]):  # noqa: UP046
    def _with_node(self, node: SelectNode) -> Self:
        return new_query(type(self), node, extract_query(self).adapter)

    def from_(self, source: Source) -> Self:
        node = select_node(self)
        if node.from_source is not None:
            raise ValueError("from_() can only be specified once")
        return self._with_node(replace(node, from_source=source.node()))

    def inner_join(self, source: Source, *, on: BooleanExpression) -> Self:
        return self._join(source, on, "inner")

    def left_join(self, source: Source, *, on: BooleanExpression) -> Self:
        return self._join(source, on, "left")

    def right_join(self, source: Source, *, on: BooleanExpression) -> Self:
        return self._join(source, on, "right")

    def full_join(self, source: Source, *, on: BooleanExpression) -> Self:
        return self._join(source, on, "full")

    def cross_join(self, source: Source) -> Self:
        join = JoinNode(source.node(), StarNode(), "cross")
        node = select_node(self)
        return self._with_node(replace(node, joins=(*node.joins, join)))

    def _join(self, source: Source, on: BooleanExpression, kind: str) -> Self:
        join = JoinNode(source.node(), on.node(), kind)
        node = select_node(self)
        return self._with_node(replace(node, joins=(*node.joins, join)))

    def where(self, predicate: BooleanExpression) -> Self:
        query_node = select_node(self)
        node = (
            predicate.node()
            if query_node.where is None
            else BinaryNode(query_node.where, "and", predicate.node())
        )
        return self._with_node(replace(query_node, where=node))

    def group_by[T](self, *expressions: Expr[T]) -> Self:
        if not expressions:
            raise ValueError("group_by requires at least one expression")
        node = select_node(self)
        return self._with_node(
            replace(node, group_by=(*node.group_by, *(x.node() for x in expressions)))
        )

    def having(self, predicate: BooleanExpression) -> Self:
        query_node = select_node(self)
        node = (
            predicate.node()
            if query_node.having is None
            else BinaryNode(query_node.having, "and", predicate.node())
        )
        return self._with_node(replace(query_node, having=node))

    def distinct(self) -> Self:
        return self._with_node(replace(select_node(self), distinct=True))

    def order_by(self, *orders: Order) -> Self:
        if not orders:
            raise ValueError("order_by requires at least one Order")
        node = select_node(self)
        return self._with_node(
            replace(node, order_by=(*node.order_by, *(x.node() for x in orders)))
        )

    def limit(self, amount: int) -> Self:
        node = select_node(self)
        if node.limit is not None:
            raise ValueError("limit() can only be specified once")
        if amount < 0:
            raise ValueError("limit must be non-negative")
        return self._with_node(replace(node, limit=amount))

    def offset(self, amount: int) -> Self:
        node = select_node(self)
        if node.offset is not None:
            raise ValueError("offset() can only be specified once")
        if amount < 0:
            raise ValueError("offset must be non-negative")
        return self._with_node(replace(node, offset=amount))

    def as_[Relation: DerivedTable](self, relation: type[Relation], alias: str) -> Relation:
        node = select_node(self)
        _validate_output_schema(node.selections, relation.output_names())
        return relation(DerivedSourceNode(node, alias), alias)

    def _compound(self, other: Self, operator: CompoundOperator) -> Self:
        _validate_compound_result_shape(self, other)
        node = select_node(self)
        return self._with_node(
            replace(node, compounds=(*node.compounds, CompoundNode(operator, select_node(other))))
        )

    def union(self, other: Self) -> Self:
        return self._compound(other, "union")

    def union_all(self, other: Self) -> Self:
        return self._compound(other, "union all")

    def intersect(self, other: Self) -> Self:
        return self._compound(other, "intersect")

    def except_(self, other: Self) -> Self:
        return self._compound(other, "except")

    def with_[CteRow](
        self, source: CteTable, query: CteQuery[CteRow], *, materialized: bool = False
    ) -> Self:
        """Add a CTE, optionally fencing it with PostgreSQL's ``AS MATERIALIZED``.

        ``query`` is a SELECT or a bounded ``INSERT``/``UPDATE``/``DELETE``
        with ``RETURNING``.  A data-modifying CTE runs exactly once for the
        whole statement, whatever the outer query does with its rows.

        ``materialized=True`` emits ``AS MATERIALIZED``, which needs relq's
        documented engine floors: SQLite 3.35.0 (the release that also added
        ``RETURNING``) or PostgreSQL 12.  relq compiles a query without a
        connection, so it cannot check a server's version here; an engine below
        the floor rejects the statement itself.
        """
        query_node = _cte_query_node(query)
        _validate_output_schema(_output_expressions(query_node), source.output_names())
        node = select_node(self)
        return self._with_node(
            replace(
                node,
                ctes=(
                    *node.ctes,
                    CteNode(source.reference, query_node, materialized=materialized),
                ),
            )
        )

    def with_recursive[CteRow](self, source: CteTable, query: SelectQuery[CteRow]) -> Self:
        """Add a recursive CTE; its query may reference its own source name."""
        name = source.reference
        _reject_declared_model(query)
        query_node = select_node(query)
        _validate_output_schema(query_node.selections, source.output_names())
        node = select_node(self)
        return self._with_node(
            replace(node, ctes=(*node.ctes, CteNode(name, query_node, recursive=True)))
        )


@dataclass(frozen=True, slots=True, init=False)
class SelectQuery[Row](_SelectQuery[Row]):
    """A SELECT that exposes raw driver tuples."""


@dataclass(frozen=True, slots=True, init=False)
class ModelSelectQuery[Model](_SelectQuery[Model]):
    """A SELECT whose rows are decoded into one declared model."""


def cte[Relation: CteTable](relation: type[Relation], name: str) -> Relation:
    """Reference a CTE declared by :meth:`SelectQuery.with_`."""
    return relation(name)


@overload
def select[A](first: Expr[A]) -> SelectQuery[tuple[A]]: ...


@overload
def select[A, B](first: Expr[A], second: Expr[B]) -> SelectQuery[tuple[A, B]]: ...


@overload
def select[A, B, C](
    first: Expr[A], second: Expr[B], third: Expr[C]
) -> SelectQuery[tuple[A, B, C]]: ...


@overload
def select[A, B, C, D](
    first: Expr[A], second: Expr[B], third: Expr[C], fourth: Expr[D]
) -> SelectQuery[tuple[A, B, C, D]]: ...


@overload
def select[A, B, C, D, E](
    first: Expr[A], second: Expr[B], third: Expr[C], fourth: Expr[D], fifth: Expr[E]
) -> SelectQuery[tuple[A, B, C, D, E]]: ...


@overload
def select[A, B, C, D, E, F](
    first: Expr[A], second: Expr[B], third: Expr[C], fourth: Expr[D], fifth: Expr[E], sixth: Expr[F]
) -> SelectQuery[tuple[A, B, C, D, E, F]]: ...


@overload
def select[A, B, C, D, E, F, G](
    first: Expr[A],
    second: Expr[B],
    third: Expr[C],
    fourth: Expr[D],
    fifth: Expr[E],
    sixth: Expr[F],
    seventh: Expr[G],
) -> SelectQuery[tuple[A, B, C, D, E, F, G]]: ...


@overload
def select[A, B, C, D, E, F, G, H](
    first: Expr[A],
    second: Expr[B],
    third: Expr[C],
    fourth: Expr[D],
    fifth: Expr[E],
    sixth: Expr[F],
    seventh: Expr[G],
    eighth: Expr[H],
) -> SelectQuery[tuple[A, B, C, D, E, F, G, H]]: ...


def select(first: object, *rest: object, **named: object) -> object:
    if named:
        raise TypeError("select expressions must be positional")
    expressions = (first, *rest)
    if len(expressions) > 8:
        raise ValueError(
            "select supports at most eight expressions; declare a DerivedTable and compose smaller projections"
        )
    nodes: list[Node] = []
    for expression in expressions:
        if not isinstance(expression, Expr):
            raise TypeError("select accepts only SQL expressions")
        nodes.append(expression.node())
    result: SelectQuery[tuple[object, ...]] = new_query(SelectQuery, SelectNode(tuple(nodes)))
    return result


@overload
def select_model[Model](
    model: type[Model], *expressions: Expression
) -> ModelSelectQuery[Model]: ...


@overload
def select_model[Model](
    model: RowAdapter[Model], *expressions: Expression
) -> ModelSelectQuery[Model]: ...


def select_model[Model](
    model: type[Model] | RowAdapter[Model], *expressions: Expression
) -> ModelSelectQuery[Model]:
    """Select a declared row model, with no fixed projection-width limit.

    The model is mapped by database executors and therefore makes the result
    shape explicit. Use ordinary :func:`select` for fast tuple results.
    """
    adapter = model if isinstance(model, RowAdapter) else row_adapter(model)
    if not expressions:
        raise ValueError("select_model requires at least one expression")
    if len(expressions) != adapter.arity:
        raise ValueError(
            f"{adapter.model_name} requires {adapter.arity} selected expressions; "
            f"received {len(expressions)}"
        )
    return new_query(
        ModelSelectQuery,
        SelectNode(tuple(expression.node() for expression in expressions)),
        adapter,
    )


def _cte_query_node[Row](query: CteQuery[Row]) -> QueryNode:
    """Return the AST behind a CTE definition, rejecting shapes a CTE can't honor."""
    _reject_declared_model(query)
    node = extract_query(query).node
    if not isinstance(node, SelectNode) and not node.returning:
        raise ValueError("a data-modifying CTE requires returning() to publish its rows")
    return node


def _reject_declared_model[Row](query: Query[Row]) -> None:
    """Refuse a CTE body that declares a row model.

    The CTE's rows are consumed by the outer query, which owns the statement's
    result shape, so an adapter attached here could never decode anything.
    Accepting one and dropping it would silently lose a declared contract.
    """
    if extract_query(query).adapter is not None:
        raise ValueError(
            "a CTE source cannot declare a row model: its rows never reach the executor. "
            "Build the CTE with select()/returning(), and decode the outer query instead"
        )


def _output_expressions(node: QueryNode) -> tuple[Node, ...]:
    """Return the expressions a query publishes as its output relation."""
    return node.selections if isinstance(node, SelectNode) else node.returning


def _validate_output_schema(expressions: tuple[Node, ...], expected: set[str]) -> None:
    actual: list[str] = []
    for selection in expressions:
        match selection:
            case AliasNode(_, alias):
                actual.append(alias)
            case ColumnNode(_, name):
                actual.append(name)
            case NullableResultNode(AliasNode(_, alias)):
                actual.append(alias)
            case NullableResultNode(ColumnNode(_, name)):
                actual.append(name)
            case _:
                raise ValueError(
                    "typed derived and CTE relations require every selected expression "
                    "to have an output alias"
                )
    if len(actual) != len(set(actual)):
        raise ValueError("typed derived and CTE relations cannot have duplicate output names")
    if set(actual) != expected:
        raise ValueError(
            "relation output schema does not match query projection: "
            f"expected {sorted(expected)!r}, got {actual!r}"
        )


def _validate_compound_result_shape(
    left: _SelectQuery[object], right: _SelectQuery[object]
) -> None:
    """Keep the public result-type transition sound across a compound."""
    left_node = select_node(left)
    right_node = select_node(right)
    if len(left_node.selections) != len(right_node.selections):
        raise ValueError(
            "compound queries require equal projection widths: "
            f"left has {len(left_node.selections)}, right has {len(right_node.selections)}"
        )
    left_adapter = extract_query(left).adapter
    right_adapter = extract_query(right).adapter
    if (left_adapter is None) != (right_adapter is None):
        raise ValueError("compound queries cannot combine tuple and declared-model result paths")
    if (
        left_adapter is not None
        and right_adapter is not None
        and not left_adapter.is_compatible_with(right_adapter)
    ):
        raise ValueError(
            "compound queries require the same declared result model and decoder layout"
        )


# See the matching note in dml.py: the two builder modules bind each other's
# names after defining their own, so with_()'s CteQuery annotation resolves at
# runtime rather than only under a type checker.
from relq.dml import CteQuery
