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
from relq.expressions.core import ConflictTarget, Expr
from relq.rows import JsonValue

_RESERVED_ATTRIBUTES = frozenset(
    {
        # Instance state relq stores on a relation. ``Column`` is a non-data
        # descriptor, so an instance attribute of the same name wins over it. A
        # column declared under one of these names would replace the descriptor
        # and break rendering, aliasing, or schema qualification.
        "_alias",
        "_reference",
        "_schema",
        "_source",
        "_state",
        "table_name",
        # Methods and properties every relation must keep.
        "as_",
        "column_names",
        "node",
        "output_names",
        "reference",
    }
)


class Source[SqlRow_co = object](NodeValue[SourceNode]):
    """A relation that can appear in ``FROM`` or ``JOIN``."""

    __slots__ = ()

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        clashes = sorted(
            {attribute for attribute, _ in _declared_columns(cls)} & _RESERVED_ATTRIBUTES
        )
        if clashes:
            names = ", ".join(clashes)
            raise TypeError(
                f"{cls.__name__} declares column attribute(s) reserved by relq: {names}. "
                "Rename the attribute and pass name=... to keep the SQL column name."
            )

    @property
    def reference(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True, slots=True, init=False)
class Column[T](Expr[T], ConflictTarget):
    """A declared table column, bound to a source when accessed."""

    python_type: TypeForm[T] | Callable[..., T]
    _name: str = ""

    def __init__(
        self, token: object, python_type: TypeForm[T] | Callable[..., T], name: str = ""
    ) -> None:
        # ``slots=True`` rebuilds the class. On CPython 3.13.0 through 3.13.13 the
        # carried-over methods keep a ``__class__`` cell that points at the
        # discarded original, so zero-argument ``super()`` raises ``TypeError``.
        # Naming the class resolves against the surviving class and walks the same
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


def _declared_columns(table_type: type[Source[object]]) -> tuple[tuple[str, Column[object]], ...]:
    """Every column a relation declares, as ``(attribute, column)`` in schema order.

    The walk covers the whole MRO. A mixin's column is reachable through
    attribute lookup like one declared on the relation, so a mixin that declared
    ``reference`` would shadow the property ``Column.__get__`` reads and recurse
    forever. Reserved-name enforcement and column discovery share this walk so
    they cannot disagree about what a relation declares.
    """
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


class Table[SqlRow_co = object](Source[SqlRow_co]):
    """Base class for a source-visible table declaration.

    ``schema`` names the SQL namespace that owns the table. It compiles as a
    separate quoted identifier, never as part of the table's own name. Only
    PostgreSQL accepts it. SQLite's qualified names address attached databases,
    so relq rejects a schema there instead of changing what the query means.
    """

    def __init__(self, name: str, *, schema: str | None = None) -> None:
        if schema == "":
            raise ValueError("table schema must not be empty")
        self.table_name = name
        # Underscore-prefixed like the alias, so a declared column named "schema"
        # cannot shadow it.
        self._schema = schema
        self._alias: str | None = None
        initialize_node(self, TableSourceNode(name, None, schema))

    @property
    def reference(self) -> str:
        return self._alias or self.table_name

    def as_(self, alias: str) -> Self:
        if not alias:
            raise ValueError("table alias must not be empty")
        result = copy(self)
        object.__setattr__(result, "table_name", self.table_name)
        result._schema = self._schema
        result._alias = alias
        initialize_node(result, TableSourceNode(result.table_name, alias, result._schema))
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
    """Declare a typed output column. Nullability belongs in ``T``."""
    return _column(python_type, name, ValueNode(None))


def column[T](
    python_type: TypeForm[T] | Callable[..., T],
    *,
    name: str = "",
) -> Column[T]:
    """Declare a typed table column. Nullability belongs in ``T``, not in flags."""
    return _column(python_type, name, ValueNode(None))


def json_column(*, name: str = "") -> Column[JsonValue]:
    """Declare a JSON/JSONB column.

    ``column(JsonValue)`` does not type-check because ``JsonValue`` is a
    recursive type alias, which a type checker rejects as a ``TypeForm`` value.
    ``JsonValue`` already admits ``None``, so a nullable JSON column needs no
    separate spelling.
    """
    return _column(cast("TypeForm[JsonValue]", JsonValue), name, ValueNode(None))


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
    return tuple(
        cast(Column[object], getattr(source, attribute))
        for attribute, _ in _declared_columns(type(source))
    )


def _declared_column_names(table_type: type[Source[object]]) -> set[str]:
    return {column.declared_name(attribute) for attribute, column in _declared_columns(table_type)}
