"""A class declared outside relq cannot smuggle an unbuilt value into a builder."""

from collections.abc import Callable

import pytest
from relq import Column, ConflictTarget, Table, column, excluded, insert_into, select

from tests.fixtures import employees


class _Unbuilt(Column[str]):
    """Type-checks as a ``Column`` but skips the construction token."""

    def __init__(self) -> None:
        pass


class _UnbuiltTarget(ConflictTarget):
    __slots__ = ()

    def __init__(self) -> None:
        pass


class _ForgedState(Column[str]):
    """Sets ``_state`` to something that is not relq's node state."""

    def __init__(self) -> None:
        object.__setattr__(self, "_state", object())


_INSERT = insert_into(employees).values(id=1, name="Ada")

_CALLS: dict[str, Callable[[], object]] = {
    "on_conflict": lambda: _INSERT.on_conflict(_Unbuilt()),
    "on_conflict target": lambda: _INSERT.on_conflict(_UnbuiltTarget()),
    "on_conflict base": lambda: _INSERT.on_conflict(employees.id, _UnbuiltTarget()),
    "from_select": lambda: insert_into(employees).from_select(
        select(employees.name).from_(employees),
        _Unbuilt(),
    ),
    "excluded": lambda: excluded(_Unbuilt()),
    "returning": lambda: _INSERT.returning(_Unbuilt()),
    "returning among built": lambda: _INSERT.returning(employees.id, _Unbuilt()),
}


@pytest.mark.parametrize("call", _CALLS.values(), ids=_CALLS.keys())
def test_an_unbuilt_column_is_a_type_error_at_the_call(call: Callable[[], object]) -> None:
    with pytest.raises(TypeError, match=r"table column|not created by a relq builder"):
        call()


def test_an_unbuilt_column_is_refused_wherever_an_expression_is_read() -> None:
    with pytest.raises(TypeError, match="not created by a relq builder"):
        select(_Unbuilt())
    with pytest.raises(TypeError, match="not created by a relq builder"):
        employees.id.eq(_Unbuilt())
    with pytest.raises(TypeError, match="not created by a relq builder"):
        _Unbuilt().is_null()


def test_a_column_with_a_forged_state_is_a_type_error_at_the_call() -> None:
    with pytest.raises(TypeError, match="table column"):
        excluded(_ForgedState())
    with pytest.raises(TypeError, match="not created by a relq builder"):
        employees.id.eq(_ForgedState())  # pyright: ignore[reportArgumentType]


def test_an_unbuilt_column_cannot_be_declared_on_a_table() -> None:
    with pytest.raises(TypeError, match="not created by a relq builder"):

        class Declared(Table):  # pyright: ignore[reportUnusedClass]
            name: Column[str] = _Unbuilt()

    with pytest.raises(TypeError, match="not created by a relq builder"):

        class Mixed(Table):  # pyright: ignore[reportUnusedClass]
            id: Column[int] = column()
            name: Column[str] = _Unbuilt()


def test_an_aliased_column_is_not_a_conflict_target() -> None:
    with pytest.raises(TypeError, match="table columns"):
        _INSERT.on_conflict(employees.id.as_("alias"))  # pyright: ignore[reportArgumentType]
