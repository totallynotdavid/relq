"""Enumeration of the query scopes nested inside a statement.

A statement's own scope and the scopes below it obey different rules: a
data-modifying CTE, for example, is only legal in the outermost ``WITH``, and a
CTE name is only visible to the scopes that ``WITH`` already reaches.  This
pass gives validation one place to ask "what is nested inside this statement,
and what is bound where?" instead of re-deriving the containment shape at each
rule.
"""

from collections.abc import Iterator

from relq._analysis.walk import walk
from relq._ast import (
    DeleteNode,
    DerivedSourceNode,
    ExistsNode,
    InNode,
    InsertNode,
    InsertRowsSourceNode,
    InsertSelectSourceNode,
    InsertValuesSourceNode,
    Node,
    QueryNode,
    ScalarSubqueryNode,
    SelectNode,
    UpdateNode,
)


def nested_queries(node: QueryNode) -> Iterator[QueryNode]:
    """Yield every query strictly inside ``node``, outermost first.

    CTE bodies, derived tables, compound arms, insert-from-select sources, and
    subquery expressions each open a scope.  ``node`` itself is never yielded.
    """
    for child, _ in nested_scopes(node):
        yield child


def nested_scopes(
    node: QueryNode, visible: frozenset[str] = frozenset()
) -> Iterator[tuple[QueryNode, frozenset[str]]]:
    """Yield every query strictly inside ``node`` with the CTE names it can see.

    ``visible`` is what encloses ``node`` itself.  A ``WITH`` clause binds its
    names in declaration order, so an earlier CTE body sees fewer of them than
    the query that follows the clause, and only a recursive CTE body sees its
    own name.
    """
    for child, scope in _child_scopes(node, visible):
        yield child, scope
        yield from nested_scopes(child, scope)


def _child_scopes(
    node: QueryNode, visible: frozenset[str]
) -> Iterator[tuple[QueryNode, frozenset[str]]]:
    """Yield the queries one query directly contains, each with its own scope."""
    bound = visible
    match node:
        case SelectNode():
            for cte in node.ctes:
                yield cte.query, (bound | {cte.name}) if cte.recursive else bound
                bound |= {cte.name}
            for source in (node.from_source, *(join.source for join in node.joins)):
                if isinstance(source, DerivedSourceNode):
                    yield source.query, bound
            yield from ((compound.query, bound) for compound in node.compounds)
        case InsertNode(source=InsertSelectSourceNode(query)):
            yield query, bound
        case InsertNode() | UpdateNode() | DeleteNode():
            pass
    for expression in _expressions(node):
        yield from ((subquery, bound) for subquery in _subqueries(expression))


def _expressions(node: QueryNode) -> Iterator[Node]:
    """Yield the expressions a query holds in its own scope."""
    match node:
        case SelectNode():
            yield from node.selections
            yield from (join.predicate for join in node.joins)
            yield from node.group_by
            yield from (order.expression for order in node.order_by)
            yield from _present(node.where, node.having)
        case InsertNode():
            yield from _insert_values(node.source)
            if node.conflict is not None:
                yield from (value for _, value in node.conflict.update_values)
            yield from node.returning
        case UpdateNode():
            yield from (value for _, value in node.values)
            yield from _present(node.where)
            yield from node.returning
        case DeleteNode():
            yield from _present(node.where)
            yield from node.returning


def _insert_values(source: object) -> Iterator[Node]:
    match source:
        case InsertValuesSourceNode(values):
            yield from (value for _, value in values)
        case InsertRowsSourceNode(rows):
            yield from (value for row in rows for _, value in row)
        case _:
            return


def _present(*nodes: Node | None) -> Iterator[Node]:
    yield from (node for node in nodes if node is not None)


def _subqueries(expression: Node) -> Iterator[SelectNode]:
    for descendant in walk(expression):
        match descendant:
            case ScalarSubqueryNode(query) | ExistsNode(query):
                yield query
            case InNode(_, values) if isinstance(values, SelectNode):
                yield values
            case _:
                pass
