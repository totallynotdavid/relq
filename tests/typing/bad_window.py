from relq import Column, Table, column, row_number, select


class Users(Table):
    id: Column[int] = column(int)


users = Users("users")

# The window function is deliberately not an expression before ``over()``.
select(row_number())

# Window ordering accepts only an explicit SQL ordering value.
row_number().over().order_by(users.id)
