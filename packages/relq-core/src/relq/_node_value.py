"""Private opaque storage for public values backed by AST nodes.

The public builder layer owns typed SQL values, not AST objects.  This module
is the sole bridge between those values and the private immutable AST.
"""

from dataclasses import dataclass
from typing import Final

from relq._ast import Node
from relq._frozen import FrozenSlots, frozen_guard

_CONSTRUCTION_TOKEN: Final = object()


@frozen_guard
@dataclass(frozen=True, slots=True)
class _NodeState[N]:
    node: N


@dataclass(frozen=True, init=False)
class NodeValue[N](FrozenSlots):
    """Private base for values that carry one immutable AST node.

    Only dataclass fields survive ``copy``. Subclasses must restore any other
    state they need after copying.
    """

    __slots__ = ("_state",)

    _state: _NodeState[N]

    def __init__(self, token: object) -> None:
        if token is not _CONSTRUCTION_TOKEN:
            raise TypeError("relq values are created by relq builders, not constructors")


class _NodeBridge(NodeValue[object]):
    __slots__ = ()

    @staticmethod
    def node[N](value: NodeValue[N]) -> N:
        state = getattr(value, "_state", None)
        if not isinstance(state, _NodeState):
            raise TypeError(f"{type(value).__name__} was not created by a relq builder")
        return state.node  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]

    @staticmethod
    def initialized[N](value: NodeValue[N]) -> bool:
        return isinstance(getattr(value, "_state", None), _NodeState)


def construction_token() -> object:
    return _CONSTRUCTION_TOKEN


def initialize_node[N](value: NodeValue[N], node: N) -> None:
    object.__setattr__(value, "_state", _NodeState(node))


def node_of[N](value: NodeValue[N]) -> N:
    return _NodeBridge.node(value)


def expression_node(value: NodeValue[Node]) -> Node:
    return node_of(value)


def has_node[N](value: NodeValue[N]) -> bool:
    """Report whether a builder factory attached this value's node.

    Every value relq constructs has one, because the factories are the only way
    past the constructor token. A class declared outside relq can inherit a
    public base such as ``Column`` and skip the token by overriding
    ``__init__``. ``node_of`` raises ``TypeError`` for such a value. A builder
    that wants its own message checks this first.
    """
    return _NodeBridge.initialized(value)
