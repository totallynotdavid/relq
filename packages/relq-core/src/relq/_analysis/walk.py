"""Shared structural descent over the private Node union.

``children`` is the single place that enumerates every ``Node`` variant's
direct ``Node``-typed children.  It never yields into a nested ``SelectNode``
(a scalar subquery's body, an ``IN (SELECT ...)`` branch, ...): those fields
are not ``Node``-typed, so scope boundaries fall out of the type system
instead of being reimplemented by every caller.  There is no catch-all case:
a new ``Node`` variant makes basedpyright flag this function as
non-exhaustive, and the trailing raise covers anything that reaches it at
runtime.  Callers that need "collect every X reachable from here" build it
from ``walk``; callers with a genuinely different traversal rule (an opaque
boundary at aggregates, a narrower propagation rule, ...) still use
``children`` for their generic-descent cases so this stays the only place
the union's shape is hand-written.
"""

from collections.abc import Iterator

from relq._ast import (
    AggregateNode,
    AliasNode,
    BetweenNode,
    BinaryNode,
    CaseNode,
    ColumnNode,
    ExcludedNode,
    ExistsNode,
    FunctionNode,
    InNode,
    Node,
    NullableResultNode,
    ScalarSubqueryNode,
    SelectNode,
    StarNode,
    TemporalAgeNode,
    TemporalArithmeticNode,
    TemporalBinNode,
    TemporalClockNode,
    TemporalDifferenceNode,
    TemporalEpochNode,
    TemporalExtractNode,
    TemporalIntervalScaleNode,
    TemporalIntervalUnaryNode,
    TemporalJustifyNode,
    TemporalMakeDateNode,
    TemporalMakeIntervalNode,
    TemporalMakeTimeNode,
    TemporalMakeTimestampNode,
    TemporalMakeTimestamptzNode,
    TemporalOverlapsNode,
    TemporalTimezoneNode,
    TemporalTruncNode,
    TemporalTruncTimestamptzNode,
    UnaryNode,
    ValueNode,
    WindowNode,
)


def children(node: Node) -> Iterator[Node]:
    """Yield a node's direct Node-typed children, never crossing into a nested SELECT."""
    match node:
        case ColumnNode() | ValueNode() | TemporalClockNode() | StarNode() | ExcludedNode():
            return
        case ScalarSubqueryNode() | ExistsNode():
            return
        case BinaryNode(left, _, right):
            yield left
            yield right
            return
        case TemporalMakeDateNode(year, month, day):
            yield year
            yield month
            yield day
            return
        case TemporalMakeTimeNode(hour, minute, second):
            yield hour
            yield minute
            yield second
            return
        case TemporalMakeTimestampNode(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
        ):
            yield year
            yield month
            yield day
            yield hour
            yield minute
            yield second
            return
        case TemporalMakeTimestamptzNode(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
            zone=zone,
        ):
            yield year
            yield month
            yield day
            yield hour
            yield minute
            yield second
            if zone is not None:
                yield zone
            return
        case TemporalMakeIntervalNode(components):
            yield from (value for _, value in components)
            return
        case TemporalEpochNode(seconds):
            yield seconds
            return
        case TemporalArithmeticNode(timestamp, _, interval):
            yield timestamp
            yield interval
            return
        case TemporalDifferenceNode(left, right) | TemporalAgeNode(left, right):
            yield left
            yield right
            return
        case TemporalIntervalUnaryNode(interval) | TemporalJustifyNode(_, interval):
            yield interval
            return
        case TemporalIntervalScaleNode(interval, _, factor):
            yield interval
            yield factor
            return
        case TemporalTimezoneNode(expression, zone):
            yield expression
            yield zone
            return
        case TemporalExtractNode(_, expression):
            yield expression
            return
        case TemporalTruncNode(_, expression):
            yield expression
            return
        case TemporalTruncTimestamptzNode(_, expression, zone):
            yield expression
            yield zone
            return
        case TemporalBinNode(stride, expression, origin):
            yield stride
            yield expression
            yield origin
            return
        case TemporalOverlapsNode(left_start, left_end, right_start, right_end):
            yield left_start
            yield left_end
            yield right_start
            yield right_end
            return
        case UnaryNode(_, operand) | AliasNode(operand, _) | NullableResultNode(operand):
            yield operand
            return
        case FunctionNode(_, arguments):
            yield from arguments
            return
        case CaseNode(branches, otherwise):
            for condition, value in branches:
                yield condition
                yield value
            yield otherwise
            return
        case AggregateNode(_, arguments, filter):
            yield from arguments
            if filter is not None:
                yield filter
            return
        case WindowNode(expression, partition_by, order_by):
            yield expression
            yield from partition_by
            yield from (order.expression for order in order_by)
            return
        case InNode(expression, values):
            yield expression
            if not isinstance(values, SelectNode):
                yield from values
            return
        case BetweenNode(expression, lower, upper, _):
            yield expression
            yield lower
            yield upper
            return
    raise TypeError(f"unsupported AST node: {node!r}")


def walk(node: Node) -> Iterator[Node]:
    """Yield ``node`` and every Node reachable through it, depth-first."""
    yield node
    for child in children(node):
        yield from walk(child)
