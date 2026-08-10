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


def test_children_of_a_leaf_node_is_empty() -> None:
    assert list(children(ColumnNode("t", "id"))) == []
    assert list(children(ValueNode(1))) == []
    assert list(children(StarNode())) == []


def test_children_of_a_single_child_node_yields_the_operand() -> None:
    operand = ColumnNode("t", "id")
    assert list(children(AliasNode(operand, "alias"))) == [operand]


def test_children_of_a_binary_node_yields_both_sides() -> None:
    left, right = ColumnNode("t", "a"), ValueNode(1)
    assert list(children(BinaryNode(left, "=", right))) == [left, right]


def test_children_of_a_function_node_yields_every_argument_in_order() -> None:
    first, second = ColumnNode("t", "name"), ValueNode("x")
    assert list(children(FunctionNode("lower", (first, second)))) == [first, second]


def test_children_never_crosses_into_a_nested_select() -> None:
    subquery = SelectNode((ColumnNode("t", "id"),))
    assert list(children(ScalarSubqueryNode(subquery))) == []
    assert list(children(ExistsNode(subquery))) == []


def test_children_of_in_node_only_descends_the_tuple_branch() -> None:
    expression = ColumnNode("t", "id")
    first, second = ValueNode(1), ValueNode(2)
    assert list(children(InNode(expression, (first, second)))) == [expression, first, second]

    subquery = SelectNode((ColumnNode("t", "id"),))
    assert list(children(InNode(expression, subquery))) == [expression]


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
    subquery = SelectNode((ColumnNode("t", "id"),))
    node = ExistsNode(subquery)

    assert list(walk(node)) == [node]
