"""Public builders keep private AST values opaque."""

import pytest
from relq import Expr, FrameBoundary, Order, value
from relq._ast import FrameBoundaryNode, OrderNode, ValueNode


def test_public_ast_backed_values_reject_direct_ast_construction() -> None:
    with pytest.raises(TypeError, match="created by relq builders"):
        Expr(ValueNode(1))
    with pytest.raises(TypeError, match="created by relq builders"):
        Order(OrderNode(ValueNode(1), "asc"))
    with pytest.raises(TypeError, match="created by relq builders"):
        FrameBoundary(FrameBoundaryNode("current row"))


def test_public_expression_values_have_no_ast_accessor() -> None:
    assert not hasattr(value(1), "node")
