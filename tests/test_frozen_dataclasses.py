"""Every frozen dataclass in the shipped packages refuses attribute changes cleanly."""

from __future__ import annotations

import dataclasses
import importlib
import pkgutil
from types import ModuleType
from typing import TYPE_CHECKING

import pytest
import relq
import relq_codegen
import relq_migrate

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

_PACKAGES = (relq, relq_codegen, relq_migrate)


def _modules(package: ModuleType) -> list[ModuleType]:
    names = [package.__name__] + [
        info.name for info in pkgutil.walk_packages(package.__path__, f"{package.__name__}.")
    ]
    return [importlib.import_module(name) for name in names]


def _frozen_dataclasses() -> dict[str, type[DataclassInstance]]:
    found: dict[str, type[DataclassInstance]] = {}
    for package in _PACKAGES:
        for module in _modules(package):
            values: list[object] = list(vars(module).values())
            for value in values:
                if (
                    isinstance(value, type)
                    and value.__module__ == module.__name__
                    and dataclasses.is_dataclass(value)
                    and value.__dataclass_params__.frozen  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                ):
                    found[f"{value.__module__}.{value.__qualname__}"] = value
    return found


_FROZEN = _frozen_dataclasses()


def test_the_walk_finds_frozen_dataclasses_in_every_package() -> None:
    for package in _PACKAGES:
        assert any(name.startswith(f"{package.__name__}.") for name in _FROZEN)


@pytest.mark.parametrize("name", _FROZEN)
def test_assigning_or_deleting_an_attribute_raises_frozen_instance_error(name: str) -> None:
    cls = _FROZEN[name]
    # The guard never reads a field, so an instance whose constructor did not run
    # is enough. Real construction is covered where each class is used.
    instance: object = object.__new__(cls)
    declared = [field.name for field in dataclasses.fields(cls)]
    # A plain subclass of a frozen dataclass is not a dataclass itself, so it may
    # take new attributes. Only its inherited fields stay frozen.
    decorated = "__dataclass_params__" in vars(cls)
    for attribute in [*(["undeclared"] if decorated else []), *declared[:1]]:
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, attribute, 1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            delattr(instance, attribute)
