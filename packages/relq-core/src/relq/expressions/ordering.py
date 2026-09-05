"""Closed ordering grammar shared by query and window clauses."""

from __future__ import annotations

from typing import Literal

from relq._ast import Node, OrderNode
from relq._node_value import NodeValue, construction_token, initialize_node, node_of


class Order(NodeValue[OrderNode]):
    """A closed ordering value created from an expression."""

    __slots__ = ()

    def nulls_first(self) -> Order:
        return self._with_nulls("first")

    def nulls_last(self) -> Order:
        return self._with_nulls("last")

    def _with_nulls(self, placement: Literal["first", "last"]) -> Order:
        node = node_of(self)
        if node.nulls is not None:
            raise ValueError("an ORDER BY expression can have only one NULL placement")
        return order_from_expression(node.expression, node.direction, placement)


def order_from_expression(
    expression: Node,
    direction: Literal["asc", "desc"],
    nulls: Literal["first", "last"] | None = None,
) -> Order:
    order = Order(construction_token())
    initialize_node(order, OrderNode(expression, direction, nulls))
    return order
