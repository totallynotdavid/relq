"""Portable aggregates, window functions, and closed frame grammar."""

import decimal
from dataclasses import dataclass, replace
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
from relq.expressions.core import (
    AverageResult,
    BooleanExpression,
    DecimalDialectNumber,
    Expr,
    Expression,
)
from relq.expressions.ordering import Order


@dataclass(frozen=True, slots=True)
class AggregateExpr[T](Expr[T]):
    def filter(self, predicate: BooleanExpression) -> AggregateExpr[T]:
        node = self._node
        if not isinstance(node, AggregateNode):
            raise TypeError("AggregateExpr must contain an AggregateNode")
        filter_node = (
            predicate.node()
            if node.filter is None
            else BinaryNode(node.filter, "and", predicate.node())
        )
        return AggregateExpr(replace(node, filter=filter_node))

    def over(self) -> WindowSpec[T]:
        node = self._node
        if not isinstance(node, AggregateNode):
            raise TypeError("AggregateExpr must contain an AggregateNode")
        return WindowSpec(WindowNode(node))


@dataclass(frozen=True, slots=True)
class WindowFunction[T]:
    _function: FunctionNode

    def over(self) -> WindowSpec[T]:
        return WindowSpec(WindowNode(self._function))


@dataclass(frozen=True, slots=True)
class WindowSpec[T](Expr[T]):
    _node: WindowNode

    def partition_by(self, *expressions: Expression) -> WindowSpec[T]:
        if not expressions:
            raise ValueError("partition_by requires at least one expression")
        return replace(
            self,
            _node=replace(
                self._node,
                partition_by=(*self._node.partition_by, *(item.node() for item in expressions)),
            ),
        )

    def order_by(self, *orders: Order) -> WindowSpec[T]:
        if not orders:
            raise ValueError("window order_by requires at least one Order")
        return replace(
            self,
            _node=replace(
                self._node, order_by=(*self._node.order_by, *(order.node() for order in orders))
            ),
        )

    def rows_between(self, start: FrameBoundary, end: FrameBoundary) -> WindowSpec[T]:
        return self._with_frame("rows", start, end)

    def range_between(self, start: FrameBoundary, end: FrameBoundary) -> WindowSpec[T]:
        return self._with_frame("range", start, end)

    def groups_between(self, start: FrameBoundary, end: FrameBoundary) -> WindowSpec[T]:
        return self._with_frame("groups", start, end)

    def exclude(self, exclusion: WindowExclusion) -> WindowSpec[T]:
        if self._node.frame is None:
            raise ValueError("window exclusions require an explicit frame")
        if self._node.exclusion is not None:
            raise ValueError("a window expression can have only one exclusion")
        return replace(self, _node=replace(self._node, exclusion=exclusion.value))

    def _with_frame(self, kind: str, start: FrameBoundary, end: FrameBoundary) -> WindowSpec[T]:
        if self._node.frame is not None:
            raise ValueError("a window expression can have only one frame")
        _validate_frame_bounds(start, end)
        return replace(
            self, _node=replace(self._node, frame=WindowFrameNode(kind, start.node(), end.node()))
        )


@dataclass(frozen=True, slots=True)
class FrameBoundary:
    _node: FrameBoundaryNode

    def node(self) -> FrameBoundaryNode:
        return self._node


class WindowExclusion(StrEnum):
    NO_OTHERS = "no others"
    CURRENT_ROW = "current row"
    GROUP = "group"
    TIES = "ties"


def unbounded_preceding() -> FrameBoundary:
    return FrameBoundary(FrameBoundaryNode("unbounded preceding"))


def preceding(amount: int) -> FrameBoundary:
    return _frame_offset("preceding", amount)


def current_row() -> FrameBoundary:
    return FrameBoundary(FrameBoundaryNode("current row"))


def following(amount: int) -> FrameBoundary:
    return _frame_offset("following", amount)


def unbounded_following() -> FrameBoundary:
    return FrameBoundary(FrameBoundaryNode("unbounded following"))


def count[T](expression: Expr[T] | None = None) -> AggregateExpr[int]:
    argument = StarNode() if expression is None else expression.node()
    return AggregateExpr(AggregateNode("count", (argument,)))


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
    if not isinstance(expression, Expr):
        raise TypeError("sum() requires a SQL expression")
    result: AggregateExpr[object] = AggregateExpr(AggregateNode("sum", (expression.node(),)))
    return result


def avg(
    expression: Expr[int] | Expr[float] | Expr[decimal.Decimal],
) -> AggregateExpr[AverageResult]:
    """Return the dialect-native average type without a false float promise."""
    return AggregateExpr(AggregateNode("avg", (expression.node(),)))


def min[T](expression: Expr[T]) -> AggregateExpr[T | None]:
    return AggregateExpr(AggregateNode("min", (expression.node(),)))


def max[T](expression: Expr[T]) -> AggregateExpr[T | None]:
    return AggregateExpr(AggregateNode("max", (expression.node(),)))


def row_number() -> WindowFunction[int]:
    return WindowFunction(FunctionNode("row_number", ()))


def rank() -> WindowFunction[int]:
    return WindowFunction(FunctionNode("rank", ()))


def dense_rank() -> WindowFunction[int]:
    return WindowFunction(FunctionNode("dense_rank", ()))


def percent_rank() -> WindowFunction[float]:
    return WindowFunction(FunctionNode("percent_rank", ()))


def cume_dist() -> WindowFunction[float]:
    return WindowFunction(FunctionNode("cume_dist", ()))


def _frame_offset(kind: str, amount: int) -> FrameBoundary:
    if isinstance(amount, bool) or amount < 0:
        raise ValueError(f"{kind} frame offset must be a non-negative integer")
    return FrameBoundary(FrameBoundaryNode(kind, amount))


def _validate_frame_bounds(start: FrameBoundary, end: FrameBoundary) -> None:
    if start.node().kind == "unbounded following":
        raise ValueError("a window frame cannot start at unbounded following")
    if end.node().kind == "unbounded preceding":
        raise ValueError("a window frame cannot end at unbounded preceding")
    if _frame_position(start.node()) > _frame_position(end.node()):
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
