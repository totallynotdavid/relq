"""A frozen guard that survives ``@dataclass(frozen=True, slots=True)``."""

from dataclasses import FrozenInstanceError, fields
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from _typeshed import DataclassInstance


def frozen_guard[C: type](cls: C) -> C:
    """Replace the frozen guard that ``slots=True`` leaves on a rebuilt class.

    On CPython 3.13.0 through 3.13.13 and 3.14.0 through 3.14.4 the generated
    ``__setattr__`` and ``__delattr__`` call ``super()`` on the discarded
    original class, which raises ``TypeError`` for a name that is not a field.
    The replacements close over the rebuilt class and behave as the generated
    ones do on a fixed release. Apply it above ``@dataclass(frozen=True,
    slots=True)``.
    """
    names = frozenset(field.name for field in fields(cast("type[DataclassInstance]", cls)))

    def guarded_set(self: object, name: str, value: object) -> None:
        if type(self) is cls or name in names:
            raise FrozenInstanceError(f"cannot assign to field {name!r}")
        super(cls, self).__setattr__(name, value)

    def guarded_delete(self: object, name: str) -> None:
        if type(self) is cls or name in names:
            raise FrozenInstanceError(f"cannot delete field {name!r}")
        super(cls, self).__delattr__(name)

    guarded_set.__qualname__ = f"{cls.__qualname__}.__setattr__"
    guarded_delete.__qualname__ = f"{cls.__qualname__}.__delattr__"
    type.__setattr__(cls, "__setattr__", guarded_set)
    type.__setattr__(cls, "__delattr__", guarded_delete)
    return cls
