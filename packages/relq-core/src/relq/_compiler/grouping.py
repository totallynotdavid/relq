"""Portable analytic and grouped-query validation.

SQLite accepts many grouped queries that PostgreSQL rejects.  This module
defines the smaller common contract instead of allowing an arbitrary SQLite
row to leak into a supposedly portable query result.
"""

from relq._analysis.walk import children
from relq._ast import AggregateNode, AliasNode, ColumnNode, Node, SelectNode, WindowNode


def validate_analytic_clauses(node: SelectNode) -> None:
    """Reject analytic placements both supported dialects reject."""
    if _contains_aggregate_or_window(node.where):
        raise ValueError(
            "WHERE cannot contain aggregate or window expressions; use HAVING or a subquery"
        )
    for expression in node.group_by:
        if isinstance(expression, AliasNode):
            raise TypeError(
                "GROUP BY expressions cannot be aliased; group the underlying expression"
            )
        if _contains_aggregate_or_window(expression):
            raise ValueError("GROUP BY cannot contain aggregate or window expressions")
    if _contains_window(node.having):
        raise ValueError("HAVING cannot contain window expressions; use a derived query")
    for expression in (*node.selections, node.having):
        if expression is not None:
            _validate_aggregate_nesting(expression)
    for order in node.order_by:
        _validate_aggregate_nesting(order.expression)


def validate_grouping(node: SelectNode) -> None:
    """Enforce the shared SQLite/PostgreSQL grouping contract.

    A grouped or aggregate query may use a local column outside an aggregate
    only when that column is structurally determined by its ``GROUP BY``
    expression.  Nested SELECTs validate separately; correlated outer columns
    are constants for this SELECT scope and therefore do not need grouping.
    ``validate_sources`` already proves every column reference reaching this
    function is either local to this SELECT or a correlated outer reference,
    so "not local" is sufficient to exempt a column here.
    """
    has_aggregate = any(
        _contains_aggregate(expression)
        for expression in (
            *node.selections,
            node.having,
            *(item.expression for item in node.order_by),
        )
        if expression is not None
    )
    if node.having is not None and not node.group_by and not has_aggregate:
        raise ValueError("HAVING requires GROUP BY or an aggregate expression")
    if not node.group_by and not has_aggregate:
        return

    local_sources = _local_sources(node)
    grouped = frozenset(node.group_by)
    for clause, expression in _grouped_expressions(node):
        ungrouped = _ungrouped_local_columns(expression, grouped, local_sources)
        if ungrouped:
            names = ", ".join(
                f"{column.source}.{column.name}"
                for column in sorted(ungrouped, key=lambda column: (column.source, column.name))
            )
            raise ValueError(f"{clause} references ungrouped local column(s): {names}")


def _local_sources(node: SelectNode) -> frozenset[str]:
    if node.from_source is None:
        return frozenset()
    return frozenset({node.from_source.reference, *(join.source.reference for join in node.joins)})


def _grouped_expressions(node: SelectNode) -> tuple[tuple[str, Node], ...]:
    expressions: list[tuple[str, Node]] = [("SELECT", expression) for expression in node.selections]
    if node.having is not None:
        expressions.append(("HAVING", node.having))
    expressions.extend(("ORDER BY", order.expression) for order in node.order_by)
    return tuple(expressions)


def _ungrouped_local_columns(
    node: Node,
    grouped: frozenset[Node],
    local_sources: frozenset[str],
) -> set[ColumnNode]:
    if node in grouped:
        return set()
    if isinstance(node, ColumnNode):
        return {node} if node.source in local_sources else set()
    if isinstance(node, AggregateNode):
        return set()
    return {
        column
        for child in children(node)
        for column in _ungrouped_local_columns(child, grouped, local_sources)
    }


def _contains_aggregate_or_window(node: Node | None) -> bool:
    return _contains_analytic(node, aggregate=True, window=True)


def _contains_window(node: Node | None) -> bool:
    return _contains_analytic(node, aggregate=False, window=True)


def _contains_aggregate(node: Node | None) -> bool:
    return _contains_analytic(node, aggregate=True, window=False)


def _contains_analytic(node: Node | None, *, aggregate: bool, window: bool) -> bool:
    if node is None:
        return False
    if isinstance(node, AggregateNode) and aggregate:
        return True
    if isinstance(node, WindowNode):
        return window
    return any(
        _contains_analytic(child, aggregate=aggregate, window=window) for child in children(node)
    )


def _validate_aggregate_nesting(node: Node) -> None:
    def visit(current: Node, inside_aggregate: bool = False) -> None:
        if isinstance(current, AggregateNode):
            if inside_aggregate:
                raise ValueError("aggregate expressions cannot contain another aggregate")
            for child in children(current):
                visit(child, True)
            return
        if isinstance(current, WindowNode):
            if inside_aggregate:
                raise ValueError("aggregate expressions cannot contain window expressions")
            for child in children(current):
                visit(child, False)
            return
        for child in children(current):
            visit(child, inside_aggregate)

    visit(node)
