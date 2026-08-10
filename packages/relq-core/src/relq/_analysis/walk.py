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
    UnaryNode,
    ValueNode,
    WindowNode,
)


def children(node: Node) -> Iterator[Node]:
    """Yield a node's direct Node-typed children, never crossing into a nested SELECT."""
    match node:
        case ColumnNode() | ValueNode() | StarNode() | ExcludedNode():
            return
        case ScalarSubqueryNode() | ExistsNode():
            return
        case BinaryNode(left, _, right):
            yield left
            yield right
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
