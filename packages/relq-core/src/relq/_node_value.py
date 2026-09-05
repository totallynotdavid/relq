"""Private opaque storage for public values backed by AST nodes.

The public builder layer owns typed SQL values, not AST objects.  This module
is the sole bridge between those values and the private immutable AST.
"""

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

    # ``__slots__`` is spelled out instead of asked for with ``slots=True``
    # because that argument rebuilds the class, and on CPython 3.13.0 through
    # 3.13.13 *and* 3.14.0 through 3.14.4 the frozen ``__setattr__`` carried
    # into the replacement still closed over the discarded original.  Every
    # subclass assignment that is not one of this class's own fields --
    # ``Table.table_name``, for example -- reached that method and raised
    # ``TypeError`` instead of being stored, so no table could be instantiated.
    # The two windows were fixed separately, in 3.13.14 and 3.14.5; both are
    # inside what ``requires-python`` admits, and CI pins 3.13.0 and 3.14.0 so
    # neither can regress unnoticed.  Declaring the layout here produces the
    # same class without the rebuild.
    __slots__ = ("_state",)

    _state: _NodeState[N]

    def __init__(self, token: object) -> None:
        if token is not _CONSTRUCTION_TOKEN:
            raise TypeError("relq values are created by relq builders, not constructors")

    # ``slots=True`` installs ``__getstate__``/``__setstate__`` on a frozen
    # slotted dataclass and a hand-written ``__slots__`` does not, so they are
    # restated here exactly as ``_dataclass_getstate``/``_dataclass_setstate``
    # would have generated them: the dataclass fields, nothing else.  Matching
    # that narrow behaviour is deliberate.  It is not fully correct -- state a
    # subclass keeps outside the fields does not survive ``copy``, which is why
    # ``Table.as_`` re-sets ``table_name`` by hand -- but it is what relq has
    # always done, and widening it here broke pickling of generic values whose
    # ``__dict__`` carries ``__orig_class__``.  Copy/pickle completeness for
    # extra subclass state is a separate, pre-existing defect.
    def __getstate__(self) -> tuple[object, ...]:
        return tuple(cast(object, getattr(self, field.name)) for field in fields(self))

    def __setstate__(self, state: tuple[object, ...]) -> None:
        for field, value in zip(fields(self), state, strict=True):
            object.__setattr__(self, field.name, value)


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
