"""Scalar typed SQL expressions and closed SQL truth expressions."""

from __future__ import annotations

import datetime
import decimal
import enum
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
    TemporalOverlapsNode,
    TemporalTimezoneNode,
    TemporalTruncNode,
    UnaryNode,
    ValueNode,
)
from relq._query import select_node
from relq.expressions.ordering import Order
from relq.rows import AwareDateTime, AwareTime, Interval, NaiveDateTime, NaiveTime

if TYPE_CHECKING:
    from relq.query import SelectQuery


type DecimalDialectNumber = int | float | decimal.Decimal
type AverageResult = float | decimal.Decimal | None
T = TypeVar("T")


class ExtractField(enum.StrEnum):
    """The closed set of PostgreSQL fields accepted by ``extract``."""

    CENTURY = "century"
    DAY = "day"
    DECADE = "decade"
    DOW = "dow"
    DOY = "doy"
    EPOCH = "epoch"
    HOUR = "hour"
    ISODOW = "isodow"
    ISOYEAR = "isoyear"
    MICROSECONDS = "microseconds"
    MILLENNIUM = "millennium"
    MILLISECONDS = "milliseconds"
    MINUTE = "minute"
    MONTH = "month"
    QUARTER = "quarter"
    SECOND = "second"
    TIMEZONE = "timezone"
    TIMEZONE_HOUR = "timezone_hour"
    TIMEZONE_MINUTE = "timezone_minute"
    WEEK = "week"
    YEAR = "year"


class TruncUnit(enum.StrEnum):
    """The closed set of PostgreSQL ``date_trunc`` units."""

    MICROSECOND = "microseconds"
    MILLISECOND = "milliseconds"
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    DECADE = "decade"
    CENTURY = "century"
    MILLENNIUM = "millennium"


# Descriptive aliases make the domain names discoverable without adding new
# enum members or widening the accepted SQL vocabulary.
DatePart = ExtractField
DateTruncUnit = TruncUnit


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


def transaction_timestamp() -> Expr[AwareDateTime]:
    return Expr(TemporalClockNode("transaction_timestamp"))


def statement_timestamp() -> Expr[AwareDateTime]:
    return Expr(TemporalClockNode("statement_timestamp"))


def clock_timestamp() -> Expr[AwareDateTime]:
    return Expr(TemporalClockNode("clock_timestamp"))


def current_date() -> Expr[datetime.date]:
    return Expr(TemporalClockNode("current_date"))


def current_time() -> Expr[AwareTime]:
    return Expr(TemporalClockNode("current_time"))


def local_time() -> Expr[NaiveTime]:
    return Expr(TemporalClockNode("local_time"))


def local_timestamp() -> Expr[NaiveDateTime]:
    return Expr(TemporalClockNode("local_timestamp"))


def make_date(
    year: int | Expr[int], month: int | Expr[int], day: int | Expr[int]
) -> Expr[datetime.date]:
    return Expr(TemporalMakeDateNode(_node(year), _node(month), _node(day)))


def make_time(
    hour: int | Expr[int], minute: int | Expr[int], second: float | Expr[float]
) -> Expr[NaiveTime]:
    return Expr(TemporalMakeTimeNode(_node(hour), _node(minute), _node(second)))


def make_timestamp(
    year: int | Expr[int],
    month: int | Expr[int],
    day: int | Expr[int],
    hour: int | Expr[int],
    minute: int | Expr[int],
    second: float | Expr[float],
) -> Expr[NaiveDateTime]:
    return Expr(
        TemporalMakeTimestampNode(
            _node(year),
            _node(month),
            _node(day),
            _node(hour),
            _node(minute),
            _node(second),
        )
    )


def make_timestamptz(
    year: int | Expr[int],
    month: int | Expr[int],
    day: int | Expr[int],
    hour: int | Expr[int],
    minute: int | Expr[int],
    second: float | Expr[float],
) -> Expr[AwareDateTime]:
    return Expr(
        TemporalMakeTimestampNode(
            _node(year),
            _node(month),
            _node(day),
            _node(hour),
            _node(minute),
            _node(second),
            aware=True,
        )
    )


def make_interval(
    *,
    years: int | Expr[int] = 0,
    months: int | Expr[int] = 0,
    weeks: int | Expr[int] = 0,
    days: int | Expr[int] = 0,
    hours: int | Expr[int] = 0,
    minutes: int | Expr[int] = 0,
    seconds: float | Expr[float] = 0.0,
) -> Expr[Interval]:
    values = (
        ("years", years),
        ("months", months),
        ("weeks", weeks),
        ("days", days),
        ("hours", hours),
        ("mins", minutes),
        ("secs", seconds),
    )
    components = tuple(
        (name, _node(value)) for name, value in values if isinstance(value, Expr) or value != 0
    )
    return Expr(TemporalMakeIntervalNode(components))


def to_timestamp(seconds: float | Expr[float]) -> Expr[AwareDateTime]:
    return Expr(TemporalEpochNode(_node(seconds)))


@overload
def age(left: Expr[NaiveDateTime], right: Expr[NaiveDateTime]) -> Expr[Interval]: ...


@overload
def age(left: Expr[AwareDateTime], right: Expr[AwareDateTime]) -> Expr[Interval]: ...


def age(
    left: Expr[NaiveDateTime] | Expr[AwareDateTime],
    right: Expr[NaiveDateTime] | Expr[AwareDateTime],
) -> Expr[Interval]:
    return Expr(TemporalAgeNode(left.node(), right.node()))


def justify_days(interval: Interval | Expr[Interval]) -> Expr[Interval]:
    return Expr(TemporalJustifyNode("justify_days", _node(interval)))


def justify_hours(interval: Interval | Expr[Interval]) -> Expr[Interval]:
    return Expr(TemporalJustifyNode("justify_hours", _node(interval)))


def justify_interval(interval: Interval | Expr[Interval]) -> Expr[Interval]:
    return Expr(TemporalJustifyNode("justify_interval", _node(interval)))


def add_interval[Timestamp: (NaiveDateTime, AwareDateTime)](
    timestamp: Expr[Timestamp], delta: Interval | Expr[Interval]
) -> Expr[Timestamp]:
    """Add a PostgreSQL interval to a timestamp expression.

    SQLite compilation rejects this closed PostgreSQL-only operation.
    """
    return Expr(TemporalArithmeticNode(timestamp.node(), "+", _node(delta)))


def subtract_interval[Timestamp: (NaiveDateTime, AwareDateTime)](
    timestamp: Expr[Timestamp], delta: Interval | Expr[Interval]
) -> Expr[Timestamp]:
    """Subtract a PostgreSQL interval from a timestamp expression.

    SQLite compilation rejects this closed PostgreSQL-only operation.
    """
    return Expr(TemporalArithmeticNode(timestamp.node(), "-", _node(delta)))


def date_difference(left: Expr[datetime.date], right: Expr[datetime.date]) -> Expr[int]:
    """Return PostgreSQL's integral day difference between two dates."""
    return Expr(TemporalDifferenceNode(left.node(), right.node()))


@overload
def time_difference(left: Expr[NaiveTime], right: Expr[NaiveTime]) -> Expr[Interval]: ...


@overload
def time_difference(left: Expr[AwareTime], right: Expr[AwareTime]) -> Expr[Interval]: ...


def time_difference(
    left: Expr[NaiveTime] | Expr[AwareTime], right: Expr[NaiveTime] | Expr[AwareTime]
) -> Expr[Interval]:
    return Expr(TemporalDifferenceNode(left.node(), right.node()))


@overload
def timestamp_difference(
    left: Expr[NaiveDateTime], right: Expr[NaiveDateTime]
) -> Expr[Interval]: ...


@overload
def timestamp_difference(
    left: Expr[AwareDateTime], right: Expr[AwareDateTime]
) -> Expr[Interval]: ...


def timestamp_difference(
    left: Expr[NaiveDateTime] | Expr[AwareDateTime],
    right: Expr[NaiveDateTime] | Expr[AwareDateTime],
) -> Expr[Interval]:
    return Expr(TemporalDifferenceNode(left.node(), right.node()))


# The verb form reads naturally beside add_interval()/subtract_interval().
subtract_dates = date_difference
subtract_times = time_difference
subtract_timestamps = timestamp_difference


def negate_interval(interval: Interval | Expr[Interval]) -> Expr[Interval]:
    return Expr(TemporalIntervalUnaryNode(_node(interval)))


def multiply_interval(
    interval: Interval | Expr[Interval], factor: float | Expr[int] | Expr[float]
) -> Expr[Interval]:
    return Expr(TemporalIntervalScaleNode(_node(interval), "*", _node(factor)))


def divide_interval(
    interval: Interval | Expr[Interval], factor: float | Expr[int] | Expr[float]
) -> Expr[Interval]:
    return Expr(TemporalIntervalScaleNode(_node(interval), "/", _node(factor)))


@overload
def at_time_zone(expression: Expr[NaiveDateTime], zone: str | Expr[str]) -> Expr[AwareDateTime]: ...


@overload
def at_time_zone(expression: Expr[AwareDateTime], zone: str | Expr[str]) -> Expr[NaiveDateTime]: ...


def at_time_zone(
    expression: object, zone: str | Expr[str]
) -> Expr[NaiveDateTime] | Expr[AwareDateTime]:
    if not isinstance(expression, Expr):
        raise TypeError("AT TIME ZONE requires a SQL temporal expression")
    if type(zone) is not str and not isinstance(zone, Expr):
        raise TypeError("AT TIME ZONE requires a text zone")
    return Expr(TemporalTimezoneNode(expression.node(), _node(zone)))


@overload
def extract(field: ExtractField, expression: Expr[datetime.date]) -> Expr[decimal.Decimal]: ...


@overload
def extract(
    field: ExtractField,
    expression: Expr[NaiveDateTime]
    | Expr[AwareDateTime]
    | Expr[NaiveTime]
    | Expr[AwareTime]
    | Expr[Interval],
) -> Expr[decimal.Decimal]: ...


def extract(
    field: ExtractField,
    expression: Expr[datetime.date]
    | Expr[NaiveDateTime]
    | Expr[AwareDateTime]
    | Expr[NaiveTime]
    | Expr[AwareTime]
    | Expr[Interval],
) -> Expr[decimal.Decimal]:
    if type(field) is not ExtractField:
        raise TypeError("extract requires an ExtractField")
    return Expr(TemporalExtractNode(field.value, expression.node()))


@overload
def date_trunc(unit: TruncUnit, expression: Expr[NaiveDateTime]) -> Expr[NaiveDateTime]: ...


@overload
def date_trunc(unit: TruncUnit, expression: Expr[AwareDateTime]) -> Expr[AwareDateTime]: ...


@overload
def date_trunc(unit: TruncUnit, expression: Expr[Interval]) -> Expr[Interval]: ...


@overload
def date_trunc(
    unit: TruncUnit, expression: Expr[AwareDateTime], zone: str | Expr[str]
) -> Expr[AwareDateTime]: ...


def date_trunc(
    unit: TruncUnit,
    expression: Expr[NaiveDateTime] | Expr[AwareDateTime] | Expr[Interval],
    zone: str | Expr[str] | None = None,
) -> Expr[NaiveDateTime] | Expr[AwareDateTime] | Expr[Interval]:
    if type(unit) is not TruncUnit:
        raise TypeError("date_trunc requires a TruncUnit")
    if zone is not None and type(zone) is not str and not isinstance(zone, Expr):
        raise TypeError("date_trunc time zone must be text or a SQL text expression")
    return Expr(
        TemporalTruncNode(unit.value, expression.node(), None if zone is None else _node(zone))
    )


@overload
def date_bin(
    stride: Interval | Expr[Interval],
    expression: Expr[NaiveDateTime],
    origin: Expr[NaiveDateTime],
) -> Expr[NaiveDateTime]: ...


@overload
def date_bin(
    stride: Interval | Expr[Interval],
    expression: Expr[AwareDateTime],
    origin: Expr[AwareDateTime],
) -> Expr[AwareDateTime]: ...


def date_bin(
    stride: Interval | Expr[Interval],
    expression: Expr[NaiveDateTime] | Expr[AwareDateTime],
    origin: Expr[NaiveDateTime] | Expr[AwareDateTime],
) -> Expr[NaiveDateTime] | Expr[AwareDateTime]:
    return Expr(TemporalBinNode(_node(stride), expression.node(), origin.node()))


@overload
def overlaps(
    left_start: Expr[datetime.date],
    left_end: Expr[datetime.date],
    right_start: Expr[datetime.date],
    right_end: Expr[datetime.date],
) -> NullablePredicate: ...


@overload
def overlaps(
    left_start: Expr[NaiveDateTime],
    left_end: Expr[NaiveDateTime],
    right_start: Expr[NaiveDateTime],
    right_end: Expr[NaiveDateTime],
) -> NullablePredicate: ...


@overload
def overlaps(
    left_start: Expr[AwareDateTime],
    left_end: Expr[AwareDateTime],
    right_start: Expr[AwareDateTime],
    right_end: Expr[AwareDateTime],
) -> NullablePredicate: ...


@overload
def overlaps(
    left_start: Expr[NaiveTime],
    left_end: Expr[NaiveTime],
    right_start: Expr[NaiveTime],
    right_end: Expr[NaiveTime],
) -> NullablePredicate: ...


@overload
def overlaps(
    left_start: Expr[AwareTime],
    left_end: Expr[AwareTime],
    right_start: Expr[AwareTime],
    right_end: Expr[AwareTime],
) -> NullablePredicate: ...


def overlaps(
    left_start: Expr[datetime.date]
    | Expr[NaiveDateTime]
    | Expr[AwareDateTime]
    | Expr[NaiveTime]
    | Expr[AwareTime],
    left_end: Expr[datetime.date]
    | Expr[NaiveDateTime]
    | Expr[AwareDateTime]
    | Expr[NaiveTime]
    | Expr[AwareTime],
    right_start: Expr[datetime.date]
    | Expr[NaiveDateTime]
    | Expr[AwareDateTime]
    | Expr[NaiveTime]
    | Expr[AwareTime],
    right_end: Expr[datetime.date]
    | Expr[NaiveDateTime]
    | Expr[AwareDateTime]
    | Expr[NaiveTime]
    | Expr[AwareTime],
) -> NullablePredicate:
    return NullablePredicate(
        TemporalOverlapsNode(
            left_start.node(), left_end.node(), right_start.node(), right_end.node()
        )
    )


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
