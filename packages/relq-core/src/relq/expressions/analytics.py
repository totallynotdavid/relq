"""Portable aggregates, window functions, and closed frame grammar."""

import decimal
from dataclasses import replace
from enum import StrEnum
from typing import overload

from relq._ast import (
    AggregateNode,
    BinaryNode,
    FrameBoundaryNode,
    FunctionNode,
    StarNode,
    WindowFrameNode,
    WindowNode,
)
from relq._node_value import (
    NodeValue,
    construction_token,
    expression_node,
    initialize_node,
    node_of,
)
from relq.expressions.core import (
    AverageResult,
    BooleanExpression,
    DecimalDialectNumber,
    Expr,
    Expression,
)
from relq.expressions.ordering import Order


class AggregateExpr[T](Expr[T]):
    def filter(self, predicate: BooleanExpression) -> AggregateExpr[T]:
        node = node_of(self)
        if not isinstance(node, AggregateNode):
            raise TypeError("AggregateExpr must contain an AggregateNode")
        filter_node = (
            node_of(predicate)
            if node.filter is None
            else BinaryNode(node.filter, "and", node_of(predicate))
        )
        return _aggregate(replace(node, filter=filter_node))

    def over(self) -> WindowSpec[T]:
        node = node_of(self)
        if not isinstance(node, AggregateNode):
            raise TypeError("AggregateExpr must contain an AggregateNode")
        return _window_spec(WindowNode(node))


class WindowFunction[T](NodeValue[FunctionNode]):
    __slots__ = ()

    def over(self) -> WindowSpec[T]:
        return _window_spec(WindowNode(node_of(self)))


class WindowSpec[T](Expr[T]):
    def partition_by(self, *expressions: Expression) -> WindowSpec[T]:
        if not expressions:
            raise ValueError("partition_by requires at least one expression")
        node = _window_node(self)
        return _window_spec(
            replace(
                node, partition_by=(*node.partition_by, *(node_of(item) for item in expressions))
            )
        )

    def order_by(self, *orders: Order) -> WindowSpec[T]:
        if not orders:
            raise ValueError("window order_by requires at least one Order")
        node = _window_node(self)
        return _window_spec(
            replace(node, order_by=(*node.order_by, *(node_of(order) for order in orders)))
        )

    def rows_between(self, start: FrameBoundary, end: FrameBoundary) -> WindowSpec[T]:
        return self._with_frame("rows", start, end)

    def range_between(self, start: FrameBoundary, end: FrameBoundary) -> WindowSpec[T]:
        return self._with_frame("range", start, end)

    def groups_between(self, start: FrameBoundary, end: FrameBoundary) -> WindowSpec[T]:
        return self._with_frame("groups", start, end)

    def exclude(self, exclusion: WindowExclusion) -> WindowSpec[T]:
        node = _window_node(self)
        if node.frame is None:
            raise ValueError("window exclusions require an explicit frame")
        if node.exclusion is not None:
            raise ValueError("a window expression can have only one exclusion")
        return _window_spec(replace(node, exclusion=exclusion.value))

    def _with_frame(self, kind: str, start: FrameBoundary, end: FrameBoundary) -> WindowSpec[T]:
        node = _window_node(self)
        if node.frame is not None:
            raise ValueError("a window expression can have only one frame")
        _validate_frame_bounds(start, end)
        return _window_spec(
            replace(node, frame=WindowFrameNode(kind, node_of(start), node_of(end)))
        )


class FrameBoundary(NodeValue[FrameBoundaryNode]):
    __slots__ = ()


class WindowExclusion(StrEnum):
    NO_OTHERS = "no others"
    CURRENT_ROW = "current row"
    GROUP = "group"
    TIES = "ties"


def unbounded_preceding() -> FrameBoundary:
    return _frame_boundary(FrameBoundaryNode("unbounded preceding"))


def preceding(amount: int) -> FrameBoundary:
    return _frame_offset("preceding", amount)


def current_row() -> FrameBoundary:
    return _frame_boundary(FrameBoundaryNode("current row"))


def following(amount: int) -> FrameBoundary:
    return _frame_offset("following", amount)


def unbounded_following() -> FrameBoundary:
    return _frame_boundary(FrameBoundaryNode("unbounded following"))


def count[T](expression: Expr[T] | None = None) -> AggregateExpr[int]:
    argument = StarNode() if expression is None else node_of(expression)
    return _aggregate(AggregateNode("count", (argument,)))


@overload
def sum[Number: (int, float)](expression: Expr[Number]) -> AggregateExpr[Number | None]: ...


@overload
def sum(expression: Expr[decimal.Decimal]) -> AggregateExpr[DecimalDialectNumber | None]: ...


def sum(expression: object) -> object:
    """Return the dialect-faithful SQL ``SUM`` result.

    SQLite does not retain a Decimal runtime value through arithmetic, while
    PostgreSQL ``numeric`` does. Use a declared row adapter when a Decimal
    domain value is required across both engines.
    """
    if not isinstance(expression, Expression):
        raise TypeError("sum() requires a SQL expression")
    result: AggregateExpr[object] = _aggregate(AggregateNode("sum", (expression_node(expression),)))
    return result


def avg(
    expression: Expr[int] | Expr[float] | Expr[decimal.Decimal],
) -> AggregateExpr[AverageResult]:
    """Return the dialect-native average type without a false float promise."""
    return _aggregate(AggregateNode("avg", (node_of(expression),)))


def min[T](expression: Expr[T]) -> AggregateExpr[T | None]:
    return _aggregate(AggregateNode("min", (node_of(expression),)))


def max[T](expression: Expr[T]) -> AggregateExpr[T | None]:
    return _aggregate(AggregateNode("max", (node_of(expression),)))


def row_number() -> WindowFunction[int]:
    return _window_function(FunctionNode("row_number", ()))


def rank() -> WindowFunction[int]:
    return _window_function(FunctionNode("rank", ()))


def dense_rank() -> WindowFunction[int]:
    return _window_function(FunctionNode("dense_rank", ()))


def percent_rank() -> WindowFunction[float]:
    return _window_function(FunctionNode("percent_rank", ()))


def cume_dist() -> WindowFunction[float]:
    return _window_function(FunctionNode("cume_dist", ()))


def _aggregate[T](node: AggregateNode) -> AggregateExpr[T]:
    expression = AggregateExpr[T](construction_token())
    initialize_node(expression, node)
    return expression


def _window_function[T](node: FunctionNode) -> WindowFunction[T]:
    function = WindowFunction[T](construction_token())
    initialize_node(function, node)
    return function


def _window_spec[T](node: WindowNode) -> WindowSpec[T]:
    expression = WindowSpec[T](construction_token())
    initialize_node(expression, node)
    return expression


def _window_node[T](expression: WindowSpec[T]) -> WindowNode:
    node = node_of(expression)
    if not isinstance(node, WindowNode):
        raise TypeError("WindowSpec must contain a WindowNode")
    return node


def _frame_boundary(node: FrameBoundaryNode) -> FrameBoundary:
    boundary = FrameBoundary(construction_token())
    initialize_node(boundary, node)
    return boundary


def _frame_offset(kind: str, amount: int) -> FrameBoundary:
    if isinstance(amount, bool) or amount < 0:
        raise ValueError(f"{kind} frame offset must be a non-negative integer")
    return _frame_boundary(FrameBoundaryNode(kind, amount))


def _validate_frame_bounds(start: FrameBoundary, end: FrameBoundary) -> None:
    if node_of(start).kind == "unbounded following":
        raise ValueError("a window frame cannot start at unbounded following")
    if node_of(end).kind == "unbounded preceding":
        raise ValueError("a window frame cannot end at unbounded preceding")
    if _frame_position(node_of(start)) > _frame_position(node_of(end)):
        raise ValueError("a window frame start cannot follow its end")


def _frame_position(boundary: FrameBoundaryNode) -> float:
    match boundary.kind:
        case "unbounded preceding":
            return float("-inf")
        case "preceding":
            assert boundary.amount is not None
            return -float(boundary.amount)
        case "current row":
            return 0.0
        case "following":
            assert boundary.amount is not None
            return float(boundary.amount)
        case "unbounded following":
            return float("inf")
        case _:
            raise ValueError(f"unsupported window frame boundary: {boundary.kind!r}")
