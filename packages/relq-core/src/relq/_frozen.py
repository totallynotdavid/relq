"""Frozen dataclasses that keep a correct ``FrozenInstanceError`` guard.

A class with hand-written ``__slots__`` inherits :class:`FrozenSlots`. Any other
class declares ``@frozen_guard`` above ``@dataclass(frozen=True, slots=True)``.
"""

from collections.abc import Sequence
from dataclasses import FrozenInstanceError, fields
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from _typeshed import DataclassInstance


def frozen_guard[C: type](cls: C) -> C:
    """Replace the frozen guard that ``slots=True`` leaves on a rebuilt class.

    On the CPython releases named in :class:`FrozenSlots` the generated
    ``__setattr__`` and ``__delattr__`` call ``super()`` on the discarded
    original class, which raises ``TypeError`` for a name that is not a field.
    The replacements close over the rebuilt class and behave as the generated
    ones do on a fixed release.
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


class FrozenSlots:
    """Base for a frozen dataclass whose ``__slots__`` are written out.

    ``@dataclass(frozen=True, slots=True)`` rebuilds the class. On CPython 3.13.0
    through 3.13.13 and 3.14.0 through 3.14.4 the rebuilt class keeps a frozen
    ``__setattr__`` and ``__delattr__`` that close over the discarded original,
    so assigning a name that is not a field raises ``TypeError`` instead of
    ``FrozenInstanceError``. Declaring ``__slots__`` and applying
    ``@dataclass(frozen=True)`` leaves the class in place, so the guards are
    correct on every supported version. A slot cannot share a name with a class
    attribute, so such a class declares no field defaults.

    A hand-written ``__slots__`` does not get the ``__getstate__`` and
    ``__setstate__`` that ``slots=True`` installs, so they live here. The state is
    a list because the generated ``__getstate__`` returns a list, and a tuple
    would change the pickle bytes. Only dataclass fields are saved, which keeps
    ``__orig_class__`` out of the pickle of a generic value.
    """

    __slots__ = ()

    def __getstate__(self) -> list[object]:
        return [
            cast(object, getattr(self, field.name))
            for field in fields(cast("DataclassInstance", self))
        ]

    def __setstate__(self, state: Sequence[object]) -> None:
        for field, value in zip(fields(cast("DataclassInstance", self)), state, strict=True):
            object.__setattr__(self, field.name, value)
