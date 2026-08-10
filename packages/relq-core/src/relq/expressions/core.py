"""Scalar typed SQL expressions and closed SQL truth expressions."""

from __future__ import annotations

import decimal
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar, overload

from relq._ast import (
    AliasNode,
    BetweenNode,
    BinaryNode,
    CaseNode,
    ExistsNode,
    FunctionNode,
    InNode,
    Node,
    NullableResultNode,
    ScalarSubqueryNode,
    UnaryNode,
    ValueNode,
)
from relq._query import select_node
from relq.expressions.ordering import Order

if TYPE_CHECKING:
    from relq.query import SelectQuery


type DecimalDialectNumber = int | float | decimal.Decimal
type AverageResult = float | decimal.Decimal | None
T = TypeVar("T")


class Expression(Protocol):
    """Non-generic structural view used where a declared model owns shape."""

    def node(self) -> Node: ...


class BooleanExpression(Protocol):
    """A SQL truth value accepted by filtering clauses."""

    def node(self) -> Node: ...


@dataclass(frozen=True, slots=True)
class Expr(Generic[T]):  # noqa: UP046 -- expressions require an invariant value parameter.
    """A SQL expression whose evaluated value has Python type ``T``."""

    _node: Node

    def node(self) -> Node:
        return self._node

    def eq(self, other: object) -> NullablePredicate:
        return _comparison_node(self.node(), "=", other)

    def ne(self, other: object) -> NullablePredicate:
        return _comparison_node(self.node(), "<>", other)

    def lt(self, other: T | Expr[T]) -> NullablePredicate:
        return NullablePredicate(BinaryNode(self.node(), "<", _node(other)))

    def lte(self, other: T | Expr[T]) -> NullablePredicate:
        return NullablePredicate(BinaryNode(self.node(), "<=", _node(other)))

    def gt(self, other: T | Expr[T]) -> NullablePredicate:
        return NullablePredicate(BinaryNode(self.node(), ">", _node(other)))

    def gte(self, other: T | Expr[T]) -> NullablePredicate:
        return NullablePredicate(BinaryNode(self.node(), ">=", _node(other)))

    def in_(self, values: Iterable[T] | SelectQuery[tuple[T]]) -> NullablePredicate:
        return _membership(self, values, False)

    def not_in(self, values: Iterable[T] | SelectQuery[tuple[T]]) -> NullablePredicate:
        return _membership(self, values, True)

    def between(self, lower: T | Expr[T], upper: T | Expr[T]) -> NullablePredicate:
        return NullablePredicate(BetweenNode(self.node(), _node(lower), _node(upper)))

    def not_between(self, lower: T | Expr[T], upper: T | Expr[T]) -> NullablePredicate:
        return NullablePredicate(BetweenNode(self.node(), _node(lower), _node(upper), negated=True))

    def is_null(self) -> Predicate:
        return Predicate(UnaryNode("is null", self.node()))

    def is_not_null(self) -> Predicate:
        return Predicate(UnaryNode("is not null", self.node()))

    def is_true(self: Expr[bool] | Expr[bool | None]) -> Predicate:
        return Predicate(UnaryNode("is true", self.node()))

    def is_false(self: Expr[bool] | Expr[bool | None]) -> Predicate:
        return Predicate(UnaryNode("is false", self.node()))

    def is_not_true(self: Expr[bool] | Expr[bool | None]) -> Predicate:
        return Predicate(UnaryNode("is not true", self.node()))

    def is_not_false(self: Expr[bool] | Expr[bool | None]) -> Predicate:
        return Predicate(UnaryNode("is not false", self.node()))

    def asc(self) -> Order:
        return Order.from_expression(self.node(), "asc")

    def desc(self) -> Order:
        return Order.from_expression(self.node(), "desc")

    def as_(self, alias: str) -> Expr[T]:
        if not alias:
            raise ValueError("expression alias must not be empty")
        return Expr(AliasNode(self.node(), alias))

    def nullable(self) -> Expr[T | None]:
        """Declare that this selected result may be SQL ``NULL``."""
        return Expr(NullableResultNode(self.node()))

    def like(self: Expr[str], pattern: str | Expr[str]) -> NullablePredicate:
        return NullablePredicate(BinaryNode(self.node(), "like", _node(pattern)))


@dataclass(frozen=True, slots=True)
class Predicate(Expr[bool]):
    """A total SQL truth value that projects as ``bool``."""

    def __bool__(self) -> bool:
        raise TypeError("SQL predicates cannot be used as Python booleans; pass them to where()")

    @overload
    def __and__(self, other: Predicate) -> Predicate: ...

    @overload
    def __and__(self, other: NullablePredicate) -> NullablePredicate: ...

    def __and__(self, other: object) -> object:
        return _combine_truth(self.node(), "and", other)

    @overload
    def __or__(self, other: Predicate) -> Predicate: ...

    @overload
    def __or__(self, other: NullablePredicate) -> NullablePredicate: ...

    def __or__(self, other: object) -> object:
        return _combine_truth(self.node(), "or", other)

    def __invert__(self) -> Predicate:
        return Predicate(UnaryNode("not", self.node()))


@dataclass(frozen=True, slots=True)
class NullablePredicate(Expr[bool | None]):
    """A SQL truth value that can become ``UNKNOWN`` and project as NULL."""

    def __bool__(self) -> bool:
        raise TypeError("SQL predicates cannot be used as Python booleans; pass them to where()")

    def __and__(self, other: BooleanExpression) -> NullablePredicate:
        return NullablePredicate(BinaryNode(self.node(), "and", other.node()))

    def __or__(self, other: BooleanExpression) -> NullablePredicate:
        return NullablePredicate(BinaryNode(self.node(), "or", other.node()))

    def __invert__(self) -> NullablePredicate:
        return NullablePredicate(UnaryNode("not", self.node()))


def value[T](item: T) -> Expr[T]:
    return Expr(ValueNode(item))


def scalar[T](query: SelectQuery[tuple[T]]) -> Expr[T | None]:
    """Embed a one-column subquery whose empty result is SQL ``NULL``."""
    return Expr(ScalarSubqueryNode(select_node(query)))


def exists[Row](query: SelectQuery[Row]) -> Predicate:
    return Predicate(ExistsNode(select_node(query)))


def not_exists[Row](query: SelectQuery[Row]) -> Predicate:
    return Predicate(ExistsNode(select_node(query), negated=True))


def coalesce[T](first: Expr[T], second: Expr[T], *rest: Expr[T]) -> Expr[T]:
    """Return the first non-NULL value using portable SQL ``COALESCE``.

    SQL requires at least two arguments. Every candidate has one declared
    result type; nullable output must be declared when every candidate can be
    NULL.
    """
    return Expr(
        FunctionNode("coalesce", (first.node(), second.node(), *(item.node() for item in rest)))
    )


def nullif[T](left: Expr[T], right: T | Expr[T]) -> Expr[T | None]:
    """Return ``NULL`` when two values compare equal."""
    return Expr(FunctionNode("nullif", (left.node(), _node(right))))


class CaseWhen[T](Protocol):
    """Public terminal grammar for a searched ``CASE`` expression.

    The concrete builder remains private: callers can add same-typed branches
    or choose an explicit terminal result, but cannot construct values from
    relq's private AST nodes.
    """

    def when(self, condition: BooleanExpression, then: T | Expr[T], /) -> CaseWhen[T]: ...

    def else_(self, otherwise: T | Expr[T], /) -> Expr[T]: ...

    def else_null(self) -> Expr[T | None]: ...


class _CaseWhen[T]:
    """Immutable implementation of the public searched-``CASE`` grammar."""

    __slots__ = ("_branches",)

    def __init__(self, branches: tuple[tuple[Node, Node], ...]) -> None:
        self._branches = branches

    def when(self, condition: BooleanExpression, then: T | Expr[T], /) -> CaseWhen[T]:
        """Return a new builder with one additional searched branch."""
        return _CaseWhen((*self._branches, (condition.node(), _node(then))))

    def else_(self, otherwise: T | Expr[T], /) -> Expr[T]:
        """Close the expression with a same-typed fallback value."""
        return Expr(CaseNode(self._branches, _node(otherwise)))

    def else_null(self) -> Expr[T | None]:
        """Close the expression with an explicit SQL ``NULL`` fallback."""
        return Expr(CaseNode(self._branches, ValueNode(None)))


def case_when[T](condition: BooleanExpression, then: T | Expr[T], /) -> CaseWhen[T]:
    """Start a closed, parameterized searched ``CASE`` expression.

    Call :meth:`CaseWhen.when` for more branches, then terminate with
    :meth:`CaseWhen.else_` or :meth:`CaseWhen.else_null`.
    """
    return _CaseWhen(((condition.node(), _node(then)),))


def _comparison_node(expression: Node, operator: str, other: object) -> NullablePredicate:
    if other is None:
        return NullablePredicate(
            UnaryNode("is null" if operator == "=" else "is not null", expression)
        )
    return NullablePredicate(BinaryNode(expression, operator, _node(other)))


def _membership[T](
    expression: Expr[T], values: Iterable[T] | SelectQuery[tuple[T]], negated: bool
) -> NullablePredicate:
    from relq.query import SelectQuery

    if isinstance(values, SelectQuery):
        return NullablePredicate(InNode(expression.node(), select_node(values), negated))
    nodes = tuple(_node(item) for item in values)
    if not nodes:
        return NullablePredicate(ValueNode(bool(negated)))
    return NullablePredicate(InNode(expression.node(), nodes, negated))


@overload
def add[Number: (int, float)](left: Expr[Number], right: Number | Expr[Number]) -> Expr[Number]: ...


@overload
def add(
    left: Expr[decimal.Decimal], right: decimal.Decimal | Expr[decimal.Decimal]
) -> Expr[DecimalDialectNumber | None]: ...


@overload
def add[Number: (int, float)](
    left: Expr[Number | None], right: Number | None | Expr[Number] | Expr[Number | None]
) -> Expr[DecimalDialectNumber | None]: ...


@overload
def add(
    left: Expr[decimal.Decimal | None],
    right: decimal.Decimal | None | Expr[decimal.Decimal] | Expr[decimal.Decimal | None],
) -> Expr[DecimalDialectNumber | None]: ...


def add(left: object, right: object) -> object:
    return _numeric_binary(left, "+", right)


@overload
def subtract[Number: (int, float)](
    left: Expr[Number], right: Number | Expr[Number]
) -> Expr[Number]: ...


@overload
def subtract(
    left: Expr[decimal.Decimal], right: decimal.Decimal | Expr[decimal.Decimal]
) -> Expr[DecimalDialectNumber | None]: ...


@overload
def subtract[Number: (int, float)](
    left: Expr[Number | None], right: Number | None | Expr[Number] | Expr[Number | None]
) -> Expr[DecimalDialectNumber | None]: ...


@overload
def subtract(
    left: Expr[decimal.Decimal | None],
    right: decimal.Decimal | None | Expr[decimal.Decimal] | Expr[decimal.Decimal | None],
) -> Expr[DecimalDialectNumber | None]: ...


def subtract(left: object, right: object) -> object:
    return _numeric_binary(left, "-", right)


@overload
def multiply[Number: (int, float)](
    left: Expr[Number], right: Number | Expr[Number]
) -> Expr[Number]: ...


@overload
def multiply(
    left: Expr[decimal.Decimal], right: decimal.Decimal | Expr[decimal.Decimal]
) -> Expr[DecimalDialectNumber | None]: ...


@overload
def multiply[Number: (int, float)](
    left: Expr[Number | None], right: Number | None | Expr[Number] | Expr[Number | None]
) -> Expr[DecimalDialectNumber | None]: ...


@overload
def multiply(
    left: Expr[decimal.Decimal | None],
    right: decimal.Decimal | None | Expr[decimal.Decimal] | Expr[decimal.Decimal | None],
) -> Expr[DecimalDialectNumber | None]: ...


def multiply(left: object, right: object) -> object:
    return _numeric_binary(left, "*", right)


@overload
def divide[Number: (int, float)](
    left: Expr[Number], right: Number | Expr[Number]
) -> Expr[Number]: ...


@overload
def divide(
    left: Expr[decimal.Decimal], right: decimal.Decimal | Expr[decimal.Decimal]
) -> Expr[DecimalDialectNumber | None]: ...


@overload
def divide[Number: (int, float)](
    left: Expr[Number | None], right: Number | None | Expr[Number] | Expr[Number | None]
) -> Expr[DecimalDialectNumber | None]: ...


@overload
def divide(
    left: Expr[decimal.Decimal | None],
    right: decimal.Decimal | None | Expr[decimal.Decimal] | Expr[decimal.Decimal | None],
) -> Expr[DecimalDialectNumber | None]: ...


def divide(left: object, right: object) -> object:
    """Return SQL division without Python's false-float rule."""
    return _numeric_binary(left, "/", right)


def _numeric_binary(left: object, operator: str, right: object) -> Expr[object]:
    if not isinstance(left, Expr):
        raise TypeError("numeric operations require a SQL expression as their left operand")
    return Expr(BinaryNode(left.node(), operator, _node(right)))


def _combine_truth(left: Node, operator: str, right: object) -> BooleanExpression:
    if isinstance(right, Predicate):
        return Predicate(BinaryNode(left, operator, right.node()))
    if isinstance(right, NullablePredicate):
        return NullablePredicate(BinaryNode(left, operator, right.node()))
    raise TypeError("SQL boolean operations require a SQL truth expression")


def _node(item: object) -> Node:
    if isinstance(item, Expr):
        return item.node()
    return ValueNode(item)
