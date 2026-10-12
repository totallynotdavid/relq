"""Public values are frozen, and a copy of one still behaves as the original."""

import copy
import dataclasses
import pickle
from collections.abc import Callable

import pytest
from relq import (
    Column,
    Interval,
    delete_from,
    insert_into,
    int_decoder,
    row_adapter,
    select,
    update,
)
from relq._compiler import compile_sqlite
from relq._compiler._model import Dialect
from relq_sqlite import QueryEvent

from tests.fixtures import EmployeeRow, employees

_VALUES: dict[str, Callable[[], object]] = {
    "column": lambda: employees.id,
    "select": lambda: select(employees.id).from_(employees),
    "insert": lambda: insert_into(employees).values(id=1, name="Ada"),
    "conflict builder": lambda: (
        insert_into(employees).values(id=1, name="Ada").on_conflict(employees.id)
    ),
    "conflict update": lambda: (
        insert_into(employees)
        .values(id=1, name="Ada")
        .on_conflict(employees.id)
        .do_update(name="Grace")
    ),
    "update": lambda: update(employees).values(name="Ada").all_rows(),
    "delete": lambda: delete_from(employees).all_rows(),
    "compiled query": lambda: compile_sqlite(select(employees.id).from_(employees)),
    "row adapter": lambda: row_adapter(EmployeeRow),
    "decoder": int_decoder,
    "query event": lambda: QueryEvent("select 1", (), 0.5, 1, None),
    "interval": lambda: Interval(1, 2, 3),
    "dialect": lambda: Dialect("postgres", "$", max_parameters=10, supports_row_locking=True),
}

_CLONES: dict[str, Callable[[object], object]] = {
    "copy": copy.copy,
    "deepcopy": copy.deepcopy,
    "pickle": lambda value: pickle.loads(pickle.dumps(value)),  # pyright: ignore[reportAny]
}


def _assign_and_delete(value: object, attribute: str) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, attribute, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        delattr(value, attribute)


@pytest.mark.parametrize("name", _VALUES)
def test_an_undeclared_attribute_cannot_be_assigned_or_deleted(name: str) -> None:
    _assign_and_delete(_VALUES[name](), "undeclared")


def test_a_declared_field_cannot_be_assigned_or_deleted() -> None:
    _assign_and_delete(employees.id, "_name")
    _assign_and_delete(Interval(days=1), "days")
    _assign_and_delete(Dialect("sqlite", "?"), "placeholder")


def test_an_interval_keeps_its_constructor() -> None:
    assert Interval() == Interval(0, 0, 0)
    assert Interval(1, 2, 3) == Interval(months=1, days=2, microseconds=3)
    assert Interval(days=7) == Interval(0, 7)
    with pytest.raises(TypeError, match="integers"):
        Interval(days=1.5)  # pyright: ignore[reportArgumentType]
    with pytest.raises(TypeError, match="integers"):
        Interval(months=True)


def test_a_dialect_keeps_its_defaults() -> None:
    dialect = Dialect("sqlite", "?")
    assert dialect.supports_returning
    assert not dialect.supports_row_locking
    assert dialect.max_parameters is None
    assert Dialect("sqlite", "?", max_parameters=3).max_parameters == 3


# A decoder wraps a function, and a function pickles only if it is importable.
@pytest.mark.parametrize("clone", _CLONES.values(), ids=_CLONES.keys())
@pytest.mark.parametrize("name", [name for name in _VALUES if name != "decoder"])
def test_a_clone_equals_the_original(name: str, clone: Callable[[object], object]) -> None:
    value = _VALUES[name]()
    cloned = clone(value)
    assert type(cloned) is type(value)
    assert cloned == value


@pytest.mark.parametrize("clone", _CLONES.values(), ids=_CLONES.keys())
def test_a_cloned_query_compiles_to_the_same_statement(clone: Callable[[object], object]) -> None:
    query = (
        insert_into(employees)
        .values(id=1, name="Ada")
        .on_conflict(employees.id)
        .do_update(name="Grace")
        .returning(employees.id)
    )
    assert compile_sqlite(clone(query)) == compile_sqlite(query)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("clone", _CLONES.values(), ids=_CLONES.keys())
def test_a_cloned_column_keeps_its_declared_name(clone: Callable[[object], object]) -> None:
    cloned = clone(employees.id)
    assert isinstance(cloned, Column)
    assert cloned.declared_name("fallback") == "id"
