"""Direct contract tests for the shared Node traversal primitives."""

from typing import cast

import pytest
from relq._analysis.walk import children, walk
from relq._ast import (
    AliasNode,
    BinaryNode,
    ColumnNode,
    ExistsNode,
    FunctionNode,
    InNode,
    Node,
    ScalarSubqueryNode,
    SelectNode,
    StarNode,
    ValueNode,
)

_ID = ColumnNode("t", "id")
_ONE = ValueNode(1)
_TWO = ValueNode(2)
_SUBQUERY = SelectNode((ColumnNode("t", "id"),))

# A nested select is its own scope, so children() never descends into it.
_CHILDREN: dict[str, tuple[Node, list[Node]]] = {
    "column": (_ID, []),
    "value": (_ONE, []),
    "star": (StarNode(), []),
    "alias": (AliasNode(_ID, "alias"), [_ID]),
    "binary": (BinaryNode(_ID, "=", _ONE), [_ID, _ONE]),
    "function": (FunctionNode("lower", (_ID, _ONE)), [_ID, _ONE]),
    "scalar subquery": (ScalarSubqueryNode(_SUBQUERY), []),
    "exists": (ExistsNode(_SUBQUERY), []),
    "in tuple": (InNode(_ID, (_ONE, _TWO)), [_ID, _ONE, _TWO]),
    "in subquery": (InNode(_ID, _SUBQUERY), [_ID]),
}


@pytest.mark.parametrize("name", _CHILDREN)
def test_children_yields_the_operands_in_order(name: str) -> None:
    node, expected = _CHILDREN[name]
    assert list(children(node)) == expected


def test_children_raises_for_a_node_outside_the_closed_union() -> None:
    with pytest.raises(TypeError, match="unsupported AST node"):
        list(children(cast(Node, object())))


def test_walk_yields_the_node_and_every_descendant() -> None:
    inner_left, inner_right = ColumnNode("t", "a"), ValueNode(1)
    inner = BinaryNode(inner_left, "+", inner_right)
    outer_right = ColumnNode("t", "b")
    root = BinaryNode(inner, "*", outer_right)

    assert list(walk(root)) == [root, inner, inner_left, inner_right, outer_right]


def test_walk_stops_at_a_nested_select_like_children_does() -> None:
    node = ExistsNode(_SUBQUERY)

    assert list(walk(node)) == [node]
