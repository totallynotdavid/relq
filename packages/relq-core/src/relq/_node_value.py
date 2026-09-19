"""Private opaque storage for public values backed by AST nodes.

The public builder layer owns typed SQL values, not AST objects.  This module
is the sole bridge between those values and the private immutable AST.
"""

from dataclasses import dataclass
from typing import Final

from relq._ast import Node

_CONSTRUCTION_TOKEN: Final = object()


@dataclass(frozen=True, slots=True)
class _NodeState[N]:
    node: N


@dataclass(frozen=True, slots=True, init=False)
class NodeValue[N]:
    """Private base for values that carry one immutable AST node."""

    _state: _NodeState[N]

    def __init__(self, token: object) -> None:
        if token is not _CONSTRUCTION_TOKEN:
            raise TypeError("relq values are created by relq builders, not constructors")


class _NodeBridge(NodeValue[object]):
    __slots__ = ()

    @staticmethod
    def node[N](value: NodeValue[N]) -> N:
        return value._state.node


def construction_token() -> object:
    """Return the token accepted by private builder constructors."""
    return _CONSTRUCTION_TOKEN


def initialize_node[N](value: NodeValue[N], node: N) -> None:
    """Attach the sole AST node while a private builder factory creates a value."""
    object.__setattr__(value, "_state", _NodeState(node))


def node_of[N](value: NodeValue[N]) -> N:
    """Extract a node for internal builder, compiler, or executor composition."""
    return _NodeBridge.node(value)


def expression_node(value: NodeValue[Node]) -> Node:
    """Extract a scalar expression node without exposing its value parameter."""
    return node_of(value)
