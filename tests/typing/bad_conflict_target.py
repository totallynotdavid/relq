from collections.abc import Callable
from typing import TypeForm

from relq import Column, ConflictTarget, Table, column, insert_into


class Users(Table):
    id: Column[int] = column(int)
    email: Column[str] = column(str)


class PretendColumn:
    """Every member a column exposes, and none of its identity."""

    @property
    def python_type(self) -> TypeForm[str] | Callable[..., str]:
        return str

    def declared_name(self, attribute: str) -> str:
        return attribute


users = Users("users")
insert = insert_into(users).values(id=1, email="a@example.com")

# A conflict target erases the column's value type, not its column-ness: names,
# predicates, and relations stay rejected even though arity is unbounded.
insert.on_conflict("email")
insert.on_conflict(users.id.eq(1))
insert.on_conflict(users)

# ConflictTarget is a nominal base, not a structural Protocol, so a lookalike
# that reproduces a column's members without inheriting it is rejected here --
# statically, matching the isinstance check _target_column_names performs.
insert.on_conflict(PretendColumn())

# And the base is inside the Expression family, so it inherits relq's own
# construction seal: NodeValue.__init__ demands a private token, and a bare
# ConflictTarget() cannot be built to be passed in the first place.
insert.on_conflict(ConflictTarget())


class Faithful(ConflictTarget[str]):
    """Inherits the real base and implements a column's members faithfully."""

    def declared_name(self, attribute: str) -> str:
        return attribute


# Subclassing the base does not help either: the seal is on construction, so a
# subclass that builds itself normally cannot produce an instance to pass.
insert.on_conflict(Faithful())
