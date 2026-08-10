from relq import Column, CteTable, cte, output_column, select, select_model

from .good import users

# Compound arms must preserve one exact result shape and mapping contract.
select(users.id).from_(users).union_all(select(users.email).from_(users))
select(users.id).from_(users).union(select_model(tuple, users.id).from_(users))


class Recursive(CteTable):
    id: Column[int] = output_column(int)


recursive = cte(Recursive, "recursive")
select(recursive.id).from_(recursive).with_recursive(
    recursive,
    select(users.id).from_(users).union_all(select(users.email).from_(users)),
)
