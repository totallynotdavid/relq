"""Declared relation schemas and descriptor-backed columns."""

from __future__ import annotations

from collections.abc import Callable
from copy import copy
from dataclasses import dataclass
from typing import Self, cast, overload

from typing_extensions import TypeForm

from relq._ast import (
    ColumnNode,
    CteSourceNode,
    ExcludedNode,
    Node,
    SourceNode,
    TableSourceNode,
    ValueNode,
)
from relq._node_value import NodeValue, construction_token, initialize_node, node_of
from relq.expressions.core import Expr, Expression


class Source[SqlRow_co = object](NodeValue[SourceNode]):
    """A relation that can appear in ``FROM`` or ``JOIN``."""

    __slots__ = ()

    @property
    def reference(self) -> str:
        raise NotImplementedError


class ConflictTarget[T_co = object](Expression):
    """A column usable as an ``ON CONFLICT`` target, with its value type widened.

    ``Expr`` -- and so ``Column`` -- is deliberately invariant in its value
    type, which means one shared type parameter cannot describe a composite
    conflict target: ``Column[str]`` and ``Column[str | None]`` never unify.  A
    conflict target is only ever read for its identity, though, so this base
    contributes a second, covariant parameter that every ``Column[X]`` widens
    to ``ConflictTarget[object]`` through.  ``on_conflict`` names it
    unparameterized and accepts any number of columns of any value types.

    It sits inside the ``Expression`` family rather than beside it so that it
    inherits the same seal every other relq value has: ``NodeValue.__init__``
    demands a private construction token that only relq's own builders hold, so
    ``ConflictTarget()`` and any subclass that constructs normally both raise
    ``TypeError``.  That is a stronger guarantee than a ``Protocol`` (which
    admitted any lookalike) or a bare abstract class (which admitted anything
    willing to implement its abstract members), and it is the mechanism relq
    already uses everywhere else rather than a second one invented here.
    """

    __slots__ = ()


@dataclass(frozen=True, slots=True, init=False)
class Column[T](ConflictTarget[T], Expr[T]):
    """A declared table column, bound to a source when accessed."""

    python_type: TypeForm[T] | Callable[..., T]
    _name: str = ""

    def __init__(
        self, token: object, python_type: TypeForm[T] | Callable[..., T], name: str = ""
    ) -> None:
        # ``slots=True`` rebuilds the class the same way, and on CPython 3.13.0
        # through 3.13.13 the methods carried over kept a ``__class__`` cell
        # pointing at the discarded original, so the zero-argument ``super()``
        # here raised ``TypeError`` -- declaring any relq column failed.  This
        # window is narrower than the one ``NodeValue`` documents: the cell was
        # repaired in 3.13.14 and no 3.14 release is affected.  Naming the class
        # explicitly resolves against the class that survives and walks the same
        # MRO on every supported version.
        super(Column, self).__init__(token)
        object.__setattr__(self, "python_type", python_type)
        object.__setattr__(self, "_name", name)

    def __set_name__(self, owner: type[Source], name: str) -> None:
        if not self._name:
            object.__setattr__(self, "_name", name)

    @overload
    def __get__(self, instance: None, owner: type[Source]) -> Column[T]: ...

    @overload
    def __get__(self, instance: Source, owner: type[Source]) -> Column[T]: ...

    def __get__(self, instance: Source | None, owner: type[Source]) -> Column[T]:
        if instance is None:
            return self
        return _column(self.python_type, self._name, ColumnNode(instance.reference, self._name))

    def declared_name(self, attribute: str) -> str:
        return self._name or attribute


class Table[SqlRow_co = object](Source[SqlRow_co]):
    """Base class for a source-visible table declaration."""

    def __init__(self, name: str) -> None:
        self.table_name = name
        self._alias: str | None = None
        initialize_node(self, TableSourceNode(name))

    @property
    def reference(self) -> str:
        return self._alias or self.table_name

    def as_(self, alias: str) -> Self:
        if not alias:
            raise ValueError("table alias must not be empty")
        result = copy(self)
        object.__setattr__(result, "table_name", self.table_name)
        result._alias = alias
        initialize_node(result, TableSourceNode(result.table_name, alias))
        return result

    def column_names(self) -> set[str]:
        return _declared_column_names(type(self))


class DerivedTable[SqlRow_co = object](Source[SqlRow_co]):
    """Base class for a typed relation bound to a query projection."""

    _reference: str

    def __init__(self, token: object) -> None:
        super().__init__(token)

    @property
    def reference(self) -> str:
        return self._reference

    @classmethod
    def output_names(cls) -> set[str]:
        return _declared_column_names(cls)


class CteTable[SqlRow_co = object](DerivedTable[SqlRow_co]):
    def __init__(self, name: str) -> None:
        if type(self) is CteTable:
            raise TypeError("CteTable must be subclassed and declare output_column fields")
        if not name:
            raise ValueError("derived table alias must not be empty")
        object.__setattr__(self, "_reference", name)
        initialize_node(self, CteSourceNode(name))


def output_column[T](python_type: TypeForm[T] | Callable[..., T], *, name: str = "") -> Column[T]:
    """Declare a typed output column.

    Nullability belongs in ``T`` (for example ``Column[str | None]``), not in
    redundant runtime metadata.
    """
    return _column(python_type, name, ValueNode(None))


def column[T](
    python_type: TypeForm[T] | Callable[..., T],
    *,
    name: str = "",
) -> Column[T]:
    """Declare a typed table column.

    Primary-key and nullability policy belong to generated DML contracts and
    the annotation respectively; keeping inert flags here created a false
    impression that the core enforced them.
    """
    return _column(python_type, name, ValueNode(None))


def excluded[T](column: Column[T]) -> Expr[T]:
    """Refer to a proposed-row field inside ``ON CONFLICT DO UPDATE``."""
    node = node_of(column)
    if not isinstance(node, ColumnNode):
        raise TypeError("excluded() requires a table column")
    expression = Expr[T](construction_token())
    initialize_node(expression, ExcludedNode(node.source, node.name))
    return expression


def _column[T](python_type: TypeForm[T] | Callable[..., T], name: str, node: Node) -> Column[T]:
    column_ = Column(construction_token(), python_type, name)
    initialize_node(column_, node)
    return column_


def source_node(source: Source[object]) -> SourceNode:
    return node_of(source)


def table_node(table: Table[object]) -> TableSourceNode:
    node = node_of(table)
    if not isinstance(node, TableSourceNode):
        raise TypeError("Table must contain a TableSourceNode")
    return node


def derived_table[Relation: DerivedTable[object]](
    relation_type: type[Relation], source: SourceNode, alias: str
) -> Relation:
    if not alias:
        raise ValueError("derived table alias must not be empty")
    relation = object.__new__(relation_type)
    object.__setattr__(relation, "_reference", alias)
    initialize_node(relation, source)
    return relation


def source_columns(source: Source[object]) -> tuple[Column[object], ...]:
    """Return the declared columns in deterministic schema order."""
    return tuple(
        cast(Column[object], getattr(source, attribute))
        for attribute, _ in _declared_columns(type(source))
    )


def _declared_column_names(table_type: type[Source[object]]) -> set[str]:
    return {column.declared_name(attribute) for attribute, column in _declared_columns(table_type)}


def _declared_columns(table_type: type[Source[object]]) -> tuple[tuple[str, Column[object]], ...]:
    declared: dict[str, Column[object]] = {}
    names: set[str] = set()
    for base in reversed(table_type.__mro__):
        for attribute, member in cast(dict[str, object], vars(base)).items():
            if isinstance(member, Column):
                declared[attribute] = cast(Column[object], member)
                names.add(member.declared_name(attribute))
    if len(names) != len(declared):
        raise TypeError("relation declarations cannot contain duplicate column names")
    return tuple(declared.items())
