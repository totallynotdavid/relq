from relq import Column, Table, case_when, coalesce, column


class Users(Table):
    id: Column[int] = column(int)
    active: Column[bool] = column(bool)


users = Users("users")

# CASE branches must have one declared result type.
case_when(users.active.is_true(), "active").when(users.active.is_false(), 0)

# SQL COALESCE has no one-argument form.
coalesce(users.id)
