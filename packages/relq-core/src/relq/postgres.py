"""PostgreSQL-only typed expressions.

Each function is a named, typed, validated expression that only PostgreSQL has.
Compiling a query that uses one for SQLite is a compile-time error. A PostgreSQL
operator relq does not model yet needs a new named function here and a new node
in the compiler, because relq has no raw SQL fragment, generic function-name
builder, or caller-supplied operator or cast target. ``docs/design-boundaries.md``
covers the other PostgreSQL-only surfaces.
"""

import uuid
from typing import cast, overload

from relq._ast import JsonTextNode, Node, RegexMatchNode, UuidCastNode, ValueNode
from relq._node_value import construction_token, expression_node, initialize_node
from relq.expressions import Expr, NullablePredicate
from relq.rows import JsonValue

__all__ = ["cast_uuid", "json_text", "regex_match"]

type TextExpr = Expr[str] | Expr[str | None]
"""A SQL text expression, whether or not its declared result is optional."""


def _expr[T](node: Node) -> Expr[T]:
    expression = Expr[T](construction_token())
    initialize_node(expression, node)
    return expression


def json_text(value: Expr[JsonValue], key: str) -> Expr[str | None]:
    """Read one JSON member as text with ``->>``.

    The result is optional for two reasons SQL does not distinguish. The member
    may be absent, or its value may be JSON ``null``. ``key`` is bound as a
    parameter.
    """
    if not key:
        raise ValueError("json_text requires a non-empty member key")
    return _expr(JsonTextNode(expression_node(value), ValueNode(key)))


def regex_match(
    value: TextExpr, pattern: str | TextExpr, *, insensitive: bool = False
) -> NullablePredicate:
    """Match a POSIX regular expression with ``~`` or ``~*``.

    As with ``LIKE``, the result is ``UNKNOWN`` when either operand is NULL, so
    this is a :class:`~relq.NullablePredicate`.
    """
    pattern_node = expression_node(pattern) if isinstance(pattern, Expr) else ValueNode(pattern)
    predicate = NullablePredicate(construction_token())
    initialize_node(predicate, RegexMatchNode(expression_node(value), pattern_node, insensitive))
    return predicate


@overload
def cast_uuid(value: Expr[str]) -> Expr[uuid.UUID]: ...


@overload
def cast_uuid(value: Expr[str | None]) -> Expr[uuid.UUID | None]: ...


def cast_uuid(value: object) -> object:
    """Cast text to ``uuid``.

    PostgreSQL raises on malformed input instead of returning NULL, so guard the
    value first, for example with :func:`regex_match`. A NULL input casts to NULL,
    so an optional argument keeps an optional result.
    """
    if not isinstance(value, Expr):
        raise TypeError("cast_uuid requires a SQL text expression")
    result: Expr[uuid.UUID | None] = _expr(
        UuidCastNode(expression_node(cast("Expr[object]", value)))
    )
    return result
