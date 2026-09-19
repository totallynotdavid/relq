"""PostgreSQL-only typed expressions.

Most of what the top-level ``relq`` package exports compiles for both
supported dialects.  The exceptions are the temporal builders it exports
(``extract``, ``date_trunc``, ``date_bin``, ``age`` and the rest of that
family), the row-locking clauses (``for_update()`` and its variants), and this
module.  This module is the one behind a separate import: each function here is
a named, typed, validated expression that only PostgreSQL has, and compiling a
query that uses one for SQLite is a compile-time error rather than a silently
different query or a runtime surprise.

That is the whole extension mechanism.  There is no raw SQL fragment, no
generic function-name builder, and no caller-supplied operator or cast target:
a PostgreSQL operator relq does not model yet needs a new named function here
and a new node in the compiler, exactly like these three.  Importing from
``relq.postgres`` is therefore the visible, greppable marker that a query uses
one of this module's extensions.

    from relq.postgres import cast_uuid, json_text, regex_match
"""

import uuid
from typing import overload

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
    """Read one JSON member as text, PostgreSQL's ``->>`` operator.

    The result is optional in two ways SQL does not distinguish: the member may
    be absent, and its value may be JSON ``null``.  ``key`` is bound as a
    parameter like any other value.
    """
    if not key:
        raise ValueError("json_text requires a non-empty member key")
    return _expr(JsonTextNode(expression_node(value), ValueNode(key)))


def regex_match(
    value: TextExpr, pattern: str | TextExpr, *, insensitive: bool = False
) -> NullablePredicate:
    """Match a POSIX regular expression, PostgreSQL's ``~`` and ``~*``.

    Like ``LIKE``, the result is ``UNKNOWN`` when either operand is NULL, so
    this is a :class:`~relq.NullablePredicate` rather than a total one.
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
    """Cast text to ``uuid``, PostgreSQL's ``::uuid``.

    PostgreSQL raises on malformed input rather than producing NULL, so guard
    the value first: :func:`regex_match` is the intended companion.  A NULL
    input casts to NULL, so an optional argument keeps an optional result.
    """
    if not isinstance(value, Expr):
        raise TypeError("cast_uuid requires a SQL text expression")
    result: Expr[uuid.UUID | None] = _expr(UuidCastNode(expression_node(value)))
    return result
