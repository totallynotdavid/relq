"""Immutable typed SELECT builder and relational composition."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Self, TypeVar, overload

from relq._ast import (
    AliasNode,
    BinaryNode,
    ColumnNode,
    CompoundNode,
    CompoundOperator,
    CteNode,
    DerivedSourceNode,
    JoinNode,
    LockClauseNode,
    Node,
    NullableResultNode,
    QueryNode,
    SelectNode,
    StarNode,
)
from relq._node_value import expression_node, node_of
from relq._query import Query, extract_query, new_query, select_node
from relq.expressions import (
    BooleanExpression,
    CteTable,
    DerivedTable,
    Expr,
    Expression,
    Order,
    Source,
    Table,
)
from relq.expressions.relations import derived_table, source_columns, source_node, table_node
from relq.rows import RowAdapter, row_adapter

Row_co = TypeVar("Row_co", covariant=True)


@dataclass(frozen=True, slots=True, init=False)
class _SelectQuery[SqlRow, Row_co](Query[Row_co]):
    def _with_node(self, node: SelectNode) -> Self:
        return new_query(type(self), node, extract_query(self).adapter)

    def from_(self, source: Source[object]) -> Self:
        node = select_node(self)
        if node.from_source is not None:
            raise ValueError("from_() can only be specified once")
        return self._with_node(replace(node, from_source=source_node(source)))

    def inner_join(self, source: Source[object], *, on: BooleanExpression) -> Self:
        return self._join(source, on, "inner")

    def left_join(self, source: Source[object], *, on: BooleanExpression) -> Self:
        return self._join(source, on, "left")

    def right_join(self, source: Source[object], *, on: BooleanExpression) -> Self:
        return self._join(source, on, "right")

    def full_join(self, source: Source[object], *, on: BooleanExpression) -> Self:
        return self._join(source, on, "full")

    def cross_join(self, source: Source[object]) -> Self:
        node = select_node(self)
        _require_from_source(node)
        join = JoinNode(source_node(source), StarNode(), "cross")
        return self._with_node(replace(node, joins=(*node.joins, join)))

    def _join(self, source: Source[object], on: BooleanExpression, kind: str) -> Self:
        node = select_node(self)
        _require_from_source(node)
        join = JoinNode(source_node(source), node_of(on), kind)
        return self._with_node(replace(node, joins=(*node.joins, join)))

    def where(self, predicate: BooleanExpression) -> Self:
        query_node = select_node(self)
        node = (
            node_of(predicate)
            if query_node.where is None
            else BinaryNode(query_node.where, "and", node_of(predicate))
        )
        return self._with_node(replace(query_node, where=node))

    def group_by[T](self, *expressions: Expr[T]) -> Self:
        if not expressions:
            raise ValueError("group_by requires at least one expression")
        node = select_node(self)
        return self._with_node(
            replace(node, group_by=(*node.group_by, *(node_of(x) for x in expressions)))
        )

    def having(self, predicate: BooleanExpression) -> Self:
        query_node = select_node(self)
        node = (
            node_of(predicate)
            if query_node.having is None
            else BinaryNode(query_node.having, "and", node_of(predicate))
        )
        return self._with_node(replace(query_node, having=node))

    def distinct(self) -> Self:
        return self._with_node(replace(select_node(self), distinct=True))

    def order_by(self, *orders: Order) -> Self:
        if not orders:
            raise ValueError("order_by requires at least one Order")
        node = select_node(self)
        return self._with_node(
            replace(node, order_by=(*node.order_by, *(node_of(x) for x in orders)))
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

    def as_[Relation: DerivedTable[object]](self, relation: type[Relation], alias: str) -> Relation:
        node = select_node(self)
        _validate_output_schema(node.selections, relation.output_names())
        return derived_table(relation, DerivedSourceNode(node, alias), alias)

    def _compound[OtherRow](
        self, other: _SelectQuery[SqlRow, OtherRow], operator: CompoundOperator
    ) -> Self:
        _validate_compound_result_shape(self, other)
        node = select_node(self)
        return self._with_node(
            replace(node, compounds=(*node.compounds, CompoundNode(operator, select_node(other))))
        )

    def union[OtherRow](self, other: _SelectQuery[SqlRow, OtherRow]) -> Self:
        return self._compound(other, "union")

    def union_all[OtherRow](self, other: _SelectQuery[SqlRow, OtherRow]) -> Self:
        return self._compound(other, "union all")

    def intersect[OtherRow](self, other: _SelectQuery[SqlRow, OtherRow]) -> Self:
        return self._compound(other, "intersect")

    def except_[OtherRow](self, other: _SelectQuery[SqlRow, OtherRow]) -> Self:
        return self._compound(other, "except")

    def with_[CteRow](
        self, source: CteTable[object], query: SelectQuery[CteRow], *, materialized: bool = False
    ) -> Self:
        """Add a SELECT CTE, optionally fencing it with PostgreSQL's ``AS MATERIALIZED``.

        A CTE that writes goes through ``with_modifying``, which returns a
        :class:`ModifyingQuery` so the statement's writes show in its type.

        ``AS MATERIALIZED`` needs SQLite 3.35.0 or PostgreSQL 12, both below
        relq's documented engine floors. The compiler never sees a connection,
        so an older engine rejects the statement itself.
        """
        return self._with_node(
            _with_cte(select_node(self), source, _select_cte_node(query), materialized)
        )

    def with_recursive[CteRow](self, source: CteTable[object], query: SelectQuery[CteRow]) -> Self:
        """Add a recursive CTE whose query may reference its own source name."""
        name = source.reference
        _reject_declared_model(query)
        query_node = select_node(query)
        _validate_output_schema(query_node.selections, source.output_names())
        node = select_node(self)
        return self._with_node(
            replace(node, ctes=(*node.ctes, CteNode(name, query_node, recursive=True)))
        )

    def _lock(
        self,
        strength: Literal["update", "no key update", "share", "key share"],
        tables: tuple[Table[object], ...],
    ) -> Self:
        sources = tuple(table_node(table) for table in tables)
        node = select_node(self)
        return self._with_node(
            replace(node, locks=(*node.locks, LockClauseNode(strength, sources)))
        )

    def _wait(self, wait: Literal["nowait", "skip locked"]) -> Self:
        node = select_node(self)
        if not node.locks:
            raise ValueError(f"{wait.replace(' ', '_')}() requires a preceding lock clause")
        latest = node.locks[-1]
        if latest.wait is not None:
            raise ValueError("a lock clause can have only one wait policy")
        return self._with_node(replace(node, locks=(*node.locks[:-1], replace(latest, wait=wait))))


@dataclass(frozen=True, slots=True, init=False)
class SelectQuery[SqlRow, Row = SqlRow](_SelectQuery[SqlRow, Row]):
    """An immutable SELECT with an optional executor-only row decoder."""

    def decode[Model](self, model: type[Model] | RowAdapter[Model]) -> SelectQuery[SqlRow, Model]:
        """Attach a row decoder that only the executor uses. The SQL is unchanged."""
        adapter = model if isinstance(model, RowAdapter) else row_adapter(model)
        if len(select_node(self).selections) != adapter.arity:
            raise ValueError(
                f"{adapter.model_name} requires {adapter.arity} result columns; "
                f"query projects {len(select_node(self).selections)}"
            )
        return new_query(SelectQuery, select_node(self), adapter)

    def with_modifying[CteRow](
        self,
        source: CteTable[object],
        query: ModifyingCteBody[CteRow],
        *,
        materialized: bool = False,
    ) -> ModifyingQuery[SqlRow, Row]:
        """Add a bounded ``INSERT``/``UPDATE``/``DELETE ... RETURNING`` as a CTE.

        The statement writes whatever the outer query does with the CTE's rows,
        and it runs exactly once. The result is a :class:`ModifyingQuery`, which
        executors accept where they accept DML with ``RETURNING`` and never
        where they accept a plain ``SelectQuery``. PostgreSQL only.
        """
        node = _with_cte(select_node(self), source, _modifying_cte_node(query), materialized)
        return new_query(ModifyingQuery, node, extract_query(self).adapter)

    def for_update(self, *of: Table[object]) -> Self:
        return self._lock("update", of)

    def for_no_key_update(self, *of: Table[object]) -> Self:
        return self._lock("no key update", of)

    def for_share(self, *of: Table[object]) -> Self:
        return self._lock("share", of)

    def for_key_share(self, *of: Table[object]) -> Self:
        return self._lock("key share", of)

    def no_wait(self) -> Self:
        return self._wait("nowait")

    def skip_locked(self) -> Self:
        return self._wait("skip locked")


@dataclass(frozen=True, slots=True, init=False)
class ModifyingQuery[SqlRow, Row = SqlRow](Query[Row]):
    """A SELECT whose ``WITH`` clause contains a data-modifying statement.

    It is terminal on purpose. Build the outer SELECT first, then attach the
    writing CTEs. A ``ModifyingQuery`` is not a ``SelectQuery``, so it cannot be
    passed to ``fetch_*`` as a read, used as a subquery, or composed into a
    compound query, where PostgreSQL rejects a writing CTE anyway.
    """

    def with_[CteRow](
        self, source: CteTable[object], query: SelectQuery[CteRow], *, materialized: bool = False
    ) -> ModifyingQuery[SqlRow, Row]:
        """Add a SELECT CTE after the writing ones, so it can read their rows."""
        node = _with_cte(select_node(self), source, _select_cte_node(query), materialized)
        return new_query(ModifyingQuery, node, extract_query(self).adapter)

    def with_modifying[CteRow](
        self,
        source: CteTable[object],
        query: ModifyingCteBody[CteRow],
        *,
        materialized: bool = False,
    ) -> ModifyingQuery[SqlRow, Row]:
        node = _with_cte(select_node(self), source, _modifying_cte_node(query), materialized)
        return new_query(ModifyingQuery, node, extract_query(self).adapter)

    def decode[Model](
        self, model: type[Model] | RowAdapter[Model]
    ) -> ModifyingQuery[SqlRow, Model]:
        """Attach a row decoder that only the executor uses. The SQL is unchanged."""
        adapter = model if isinstance(model, RowAdapter) else row_adapter(model)
        if len(select_node(self).selections) != adapter.arity:
            raise ValueError(
                f"{adapter.model_name} requires {adapter.arity} result columns; "
                f"query projects {len(select_node(self).selections)}"
            )
        return new_query(ModifyingQuery, select_node(self), adapter)


def cte[Relation: CteTable[object]](relation: type[Relation], name: str) -> Relation:
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
    nodes = _selection_nodes((first, *rest), limit=8, context="select")
    result: SelectQuery[tuple[object, ...]] = new_query(SelectQuery, SelectNode(nodes))
    return result


def select_all_from[SqlRow](source: Source[SqlRow]) -> SelectQuery[SqlRow]:
    """Project every declared column of one relation in schema order."""
    columns = source_columns(source)
    if not columns:
        raise ValueError("select_all_from requires a relation with declared columns")
    return new_query(SelectQuery, SelectNode(tuple(expression_node(column) for column in columns)))


def _selection_nodes(
    expressions: tuple[object, ...], *, limit: int, context: str
) -> tuple[Node, ...]:
    if len(expressions) > limit:
        raise ValueError(
            f"{context} supports at most {limit} expressions; "
            "declare a DerivedTable and compose smaller projections"
        )
    return tuple(expression_node(expression) for expression in _expressions(expressions, context))


def _expressions(expressions: tuple[object, ...], context: str) -> tuple[Expression, ...]:
    typed: list[Expression] = []
    for expression in expressions:
        if not isinstance(expression, Expression):
            raise TypeError(f"{context} accepts only SQL expressions")
        typed.append(expression)
    return tuple(typed)


def _require_from_source(node: SelectNode) -> None:
    if node.from_source is None:
        raise ValueError("joins require a preceding from_(...) clause")


def _select_cte_node[Row](query: SelectQuery[Row]) -> SelectNode:
    _reject_declared_model(query)
    return select_node(query)


def _modifying_cte_node[Row](query: ModifyingCteBody[Row]) -> QueryNode:
    _reject_declared_model(query)
    node = extract_query(query).node
    if isinstance(node, SelectNode):
        raise TypeError("with_modifying() takes INSERT, UPDATE, or DELETE; use with_() for SELECT")
    if not node.returning:
        raise ValueError("a data-modifying CTE requires returning() to publish its rows")
    return node


def _with_cte(
    node: SelectNode, source: CteTable[object], query_node: QueryNode, materialized: bool
) -> SelectNode:
    _validate_output_schema(_output_expressions(query_node), source.output_names())
    cte_node = CteNode(source.reference, query_node, materialized=materialized)
    return replace(node, ctes=(*node.ctes, cte_node))


def _reject_declared_model[Row](query: Query[Row]) -> None:
    """Refuse a CTE body that declares a row model.

    The outer query owns the statement's result shape, so an adapter attached
    to a CTE body would never decode anything. Dropping it silently would lose
    a declared contract.
    """
    if extract_query(query).adapter is not None:
        raise ValueError(
            "a CTE source cannot declare a row model: its rows never reach the executor. "
            "Build the CTE with select()/returning(), and decode the outer query instead"
        )


def _output_expressions(node: QueryNode) -> tuple[Node, ...]:
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


def _validate_compound_result_shape[LeftSqlRow, LeftRow, RightSqlRow, RightRow](
    left: _SelectQuery[LeftSqlRow, LeftRow], right: _SelectQuery[RightSqlRow, RightRow]
) -> None:
    left_node = select_node(left)
    right_node = select_node(right)
    if len(left_node.selections) != len(right_node.selections):
        raise ValueError(
            "compound queries require equal projection widths: "
            f"left has {len(left_node.selections)}, right has {len(right_node.selections)}"
        )


# Imported after the definitions above for the reason given in dml.py.
from relq.dml import ModifyingCteBody
