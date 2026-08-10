"""Closed ordering grammar shared by query and window clauses."""

from dataclasses import dataclass, replace
from typing import Literal

from relq._ast import Node, OrderNode


@dataclass(frozen=True, slots=True)
class Order:
    _node: OrderNode

    @classmethod
    def from_expression(cls, expression: Node, direction: Literal["asc", "desc"]) -> Order:
        return cls(OrderNode(expression, direction))

    def node(self) -> OrderNode:
        return self._node

    def nulls_first(self) -> Order:
        return self._with_nulls("first")

    def nulls_last(self) -> Order:
        return self._with_nulls("last")

    def _with_nulls(self, placement: Literal["first", "last"]) -> Order:
        if self._node.nulls is not None:
            raise ValueError("an ORDER BY expression can have only one NULL placement")
        return replace(self, _node=replace(self._node, nulls=placement))
