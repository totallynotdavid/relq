"""CTE-reference analysis shared by composition builders and the compiler."""

from relq._analysis.walk import walk
from relq._ast import (
    CteSourceNode,
    DerivedSourceNode,
    ExistsNode,
    InNode,
    Node,
    ScalarSubqueryNode,
    SelectNode,
    SourceNode,
)


def cte_references(node: SelectNode) -> set[str]:
    """Return CTE names used anywhere in a SELECT tree, including subqueries."""
    references: set[str] = set()

    def visit_select(select: SelectNode) -> None:
        if select.from_source is not None:
            visit_source(select.from_source)
        for join in select.joins:
            visit_source(join.source)
            visit_expression(join.predicate)
        for expression in select.selections:
            visit_expression(expression)
        if select.where is not None:
            visit_expression(select.where)
        for expression in select.group_by:
            visit_expression(expression)
        if select.having is not None:
            visit_expression(select.having)
        for order in select.order_by:
            visit_expression(order.expression)
        for compound in select.compounds:
            visit_select(compound.query)

    def visit_source(source: SourceNode) -> None:
        if isinstance(source, CteSourceNode):
            references.add(source.name)
        elif isinstance(source, DerivedSourceNode):
            visit_select(source.query)

    def visit_expression(expression: Node) -> None:
        for descendant in walk(expression):
            match descendant:
                case ScalarSubqueryNode(query) | ExistsNode(query):
                    visit_select(query)
                case InNode(_, values) if isinstance(values, SelectNode):
                    visit_select(values)
                case _:
                    pass

    visit_select(node)
    return references
