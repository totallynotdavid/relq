"""Declared relation schemas and descriptor-backed columns."""

from copy import copy
from dataclasses import dataclass, replace
from typing import Self, TypeForm, cast, overload

from relq._ast import (
    ColumnNode,
    CteSourceNode,
    ExcludedNode,
    SourceNode,
    TableSourceNode,
    ValueNode,
)
from relq.expressions.core import Expr


class Source:
    """A relation that can appear in ``FROM`` or ``JOIN``."""

    @property
    def reference(self) -> str:
        raise NotImplementedError

    def node(self) -> SourceNode:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Column[T](Expr[T]):
    """A declared table column, bound to a source when accessed."""

    python_type: TypeForm[T]
    _name: str = ""

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
        return replace(self, _node=ColumnNode(instance.reference, self._name))

    def declared_name(self, attribute: str) -> str:
        return self._name or attribute


class Table(Source):
    """Base class for a source-visible table declaration."""

    def __init__(self, name: str) -> None:
        self.table_name = name
        self._alias: str | None = None

    @property
    def reference(self) -> str:
        return self._alias or self.table_name

    def as_(self, alias: str) -> Self:
        if not alias:
            raise ValueError("table alias must not be empty")
        result = copy(self)
        result._alias = alias
        return result

    def node(self) -> TableSourceNode:
        return TableSourceNode(self.table_name, self._alias)

    def column_names(self) -> set[str]:
        return _declared_column_names(type(self))


class DerivedTable(Source):
    """Base class for a typed relation bound to a query projection."""

    def __init__(self, source: SourceNode, alias: str) -> None:
        if not alias:
            raise ValueError("derived table alias must not be empty")
        self._source = source
        self._reference = alias

    @property
    def reference(self) -> str:
        return self._reference

    def node(self) -> SourceNode:
        return self._source

    @classmethod
    def output_names(cls) -> set[str]:
        return _declared_column_names(cls)


class CteTable(DerivedTable):
    def __init__(self, name: str) -> None:
        if type(self) is CteTable:
            raise TypeError("CteTable must be subclassed and declare output_column fields")
        super().__init__(CteSourceNode(name), name)


def output_column[T](python_type: TypeForm[T], *, name: str = "") -> Column[T]:
    """Declare a typed output column.

    Nullability belongs in ``T`` (for example ``Column[str | None]``), not in
    redundant runtime metadata.
    """
    return Column(ValueNode(None), python_type, name)


def column[T](
    python_type: TypeForm[T],
    *,
    name: str = "",
) -> Column[T]:
    """Declare a typed table column.

    Primary-key and nullability policy belong to generated DML contracts and
    the annotation respectively; keeping inert flags here created a false
    impression that the core enforced them.
    """
    return Column(ValueNode(None), python_type, name)


def excluded[T](column: Column[T]) -> Expr[T]:
    """Refer to a proposed-row field inside ``ON CONFLICT DO UPDATE``."""
    node = column.node()
    if not isinstance(node, ColumnNode):
        raise TypeError("excluded() requires a table column")
    return Expr(ExcludedNode(node.source, node.name))


def _declared_column_names(table_type: type[Source]) -> set[str]:
    names: set[str] = set()
    for base in reversed(table_type.__mro__):
        for attribute, member in cast(dict[str, object], vars(base)).items():
            if isinstance(member, Column):
                names.add(member.declared_name(attribute))
    return names
