"""Shared structural descent over the private Node union.

``children`` is the only place that enumerates each ``Node`` variant's direct
``Node``-typed children. It never yields into a nested ``SelectNode``, such as a
scalar subquery's body or an ``IN (SELECT ...)`` branch. Those fields are not
``Node``-typed, so the types draw the scope boundary and no caller has to.
Callers that collect everything reachable use ``walk``. Callers with their own
traversal rule, such as an opaque boundary at aggregates, still use ``children``
for generic descent.
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
    JsonTextNode,
    Node,
    NullableResultNode,
    RegexMatchNode,
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
    UuidCastNode,
    ValueNode,
    WindowNode,
)


def children(node: Node) -> Iterator[Node]:
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
        case (
            UnaryNode(_, operand)
            | AliasNode(operand, _)
            | NullableResultNode(operand)
            | UuidCastNode(operand)
        ):
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
        case JsonTextNode(value, key):
            yield value
            yield key
            return
        case RegexMatchNode(value, pattern, _):
            yield value
            yield pattern
            return
    # There is deliberately no catch-all case. A new Node variant makes
    # basedpyright report the match above as non-exhaustive.
    raise TypeError(f"unsupported AST node: {node!r}")


def walk(node: Node) -> Iterator[Node]:
    """Yield ``node`` and every Node reachable through it, depth-first."""
    yield node
    for child in children(node):
        yield from walk(child)
