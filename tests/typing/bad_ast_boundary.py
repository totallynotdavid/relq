from dataclasses import dataclass

from relq import Column, Table, column, insert_into, select, value
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
select(PretendExpression()).decode(UserRow)

# ON CONFLICT takes an unbounded number of columns, but ConflictTarget is
# narrower than Expression: a plain value expression and a structural
# lookalike are both rejected, so widening the arity did not widen the kind.
insert_into(users).values(id=1).on_conflict(users.id, value(1))
insert_into(users).values(id=1).on_conflict(PretendExpression())
