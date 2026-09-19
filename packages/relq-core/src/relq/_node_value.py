"""Private opaque storage for public values backed by AST nodes.

The public builder layer owns typed SQL values, not AST objects.  This module
is the sole bridge between those values and the private immutable AST.
"""

from collections.abc import Sequence
from dataclasses import dataclass, fields
from typing import Final, cast

from relq._ast import Node

_CONSTRUCTION_TOKEN: Final = object()


@dataclass(frozen=True, slots=True)
class _NodeState[N]:
    node: N


@dataclass(frozen=True, init=False)
class NodeValue[N]:
    """Private base for values that carry one immutable AST node."""

    # ``__slots__`` is declared by hand because ``slots=True`` rebuilds the class.
    # On CPython 3.13.0 through 3.13.13 and 3.14.0 through 3.14.4 the rebuilt
    # class keeps a frozen ``__setattr__`` that closes over the discarded
    # original. Assigning any non-field attribute on a subclass, such as
    # ``Table.table_name``, then raises ``TypeError``. CI pins 3.13.0 and 3.14.0
    # so this cannot regress unnoticed.
    __slots__ = ("_state",)

    _state: _NodeState[N]

    def __init__(self, token: object) -> None:
        if token is not _CONSTRUCTION_TOKEN:
            raise TypeError("relq values are created by relq builders, not constructors")

    # A hand-written ``__slots__`` does not get the ``__getstate__`` and
    # ``__setstate__`` that ``slots=True`` installs, so they are restated here.
    # The state is a list because the generated ``__getstate__`` returns a list,
    # and a tuple would change the pickle bytes. Only dataclass fields are saved.
    # State a subclass keeps elsewhere does not survive ``copy``, which is why
    # ``Table.as_`` sets ``table_name`` again by hand. Saving more breaks pickling
    # of generic values whose ``__dict__`` holds ``__orig_class__``.
    def __getstate__(self) -> list[object]:
        return [cast(object, getattr(self, field.name)) for field in fields(self)]

    def __setstate__(self, state: Sequence[object]) -> None:
        for field, value in zip(fields(self), state, strict=True):
            object.__setattr__(self, field.name, value)


class _NodeBridge(NodeValue[object]):
    __slots__ = ()

    @staticmethod
    def node[N](value: NodeValue[N]) -> N:
        return value._state.node

    @staticmethod
    def initialized[N](value: NodeValue[N]) -> bool:
        return hasattr(value, "_state")


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
    nominal public base such as ``ConflictTarget`` and skip the token by
    overriding ``__init__``. A builder that accepts a base rather than a
    concrete type checks this first and raises its own error.
    """
    return _NodeBridge.initialized(value)
