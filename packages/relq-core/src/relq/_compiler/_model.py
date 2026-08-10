"""Compiler value objects shared by the compilation façade and renderer."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Dialect:
    """The limited dialect variation supported by relq's renderer."""

    name: str
    placeholder: str
    supports_returning: bool = True
    max_parameters: int | None = None


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    """Database-ready SQL plus its separately bound parameter values."""

    sql: str
    parameters: tuple[object, ...]
