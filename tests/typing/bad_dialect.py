from relq import Column, Table, column, select
from relq._compiler import compile_sqlite


class Users(Table):
    id: Column[int] = column(int)


users = Users("users")

# FOR UPDATE is PostgreSQL-only; compile_sqlite() must reject it statically.
compile_sqlite(select(users.id).from_(users).for_update())
