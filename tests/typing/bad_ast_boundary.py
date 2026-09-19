from dataclasses import dataclass

from relq import Column, Table, column, select_model, value
from relq._ast import Node


class Users(Table):
    id: Column[int] = column(int)


@dataclass
class UserRow:
    identifier: int


class PretendExpression:
    def node(self) -> Node:
        raise NotImplementedError


users = Users("users")

# The AST bridge is private: public builder values do not expose a node accessor.
value(1).node()
users.id.asc().node()

# Model projections accept relq's nominal expression values, not user-defined
# structural lookalikes that could smuggle in an arbitrary AST node.
select_model(UserRow, PretendExpression())
