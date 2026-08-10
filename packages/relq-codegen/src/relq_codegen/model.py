"""Normalized database metadata and explicit codegen type policy."""

from __future__ import annotations

import keyword
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True, order=True)
class Import:
    """A validated import declaration required by generated source."""

    module: str
    names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.module or not all(part.isidentifier() for part in self.module.split(".")):
            raise ValueError(f"import module must be a dotted identifier: {self.module!r}")
        if any(not name.isidentifier() or keyword.iskeyword(name) for name in self.names):
            raise ValueError(f"import names must be identifiers: {self.names!r}")

    def render(self) -> str:
        return (
            f"import {self.module}"
            if not self.names
            else f"from {self.module} import {', '.join(self.names)}"
        )


class RenderExpression:
    """A generated Python expression and the imports needed to evaluate it."""

    def render(self) -> str:
        raise NotImplementedError

    def imports(self) -> frozenset[Import]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Name(RenderExpression):
    value: str
    dependencies: frozenset[Import] = frozenset()

    def __post_init__(self) -> None:
        if not self.value.isidentifier() or keyword.iskeyword(self.value):
            raise ValueError(f"generated name must be an identifier: {self.value!r}")

    def render(self) -> str:
        return self.value

    def imports(self) -> frozenset[Import]:
        return self.dependencies


@dataclass(frozen=True, slots=True)
class Attribute(RenderExpression):
    parent: RenderExpression
    name: str

    def __post_init__(self) -> None:
        if not self.name.isidentifier() or keyword.iskeyword(self.name):
            raise ValueError(f"generated attribute must be an identifier: {self.name!r}")

    def render(self) -> str:
        return f"{self.parent.render()}.{self.name}"

    def imports(self) -> frozenset[Import]:
        return self.parent.imports()


@dataclass(frozen=True, slots=True)
class Subscript(RenderExpression):
    parent: RenderExpression
    arguments: tuple[RenderExpression, ...]

    def render(self) -> str:
        return (
            f"{self.parent.render()}[{', '.join(argument.render() for argument in self.arguments)}]"
        )

    def imports(self) -> frozenset[Import]:
        return self.parent.imports().union(*(argument.imports() for argument in self.arguments))


@dataclass(frozen=True, slots=True)
class StringLiteral(RenderExpression):
    value: str

    def render(self) -> str:
        return repr(self.value)

    def imports(self) -> frozenset[Import]:
        return frozenset()


@dataclass(frozen=True, slots=True)
class Call(RenderExpression):
    function: RenderExpression
    arguments: tuple[RenderExpression, ...] = ()

    def render(self) -> str:
        return (
            f"{self.function.render()}("
            + ", ".join(argument.render() for argument in self.arguments)
            + ")"
        )

    def imports(self) -> frozenset[Import]:
        return self.function.imports().union(*(argument.imports() for argument in self.arguments))


@dataclass(frozen=True, slots=True)
class Union(RenderExpression):
    """A closed union annotation assembled from generated expressions."""

    members: tuple[RenderExpression, ...]

    def __post_init__(self) -> None:
        if len(self.members) < 2:
            raise ValueError("generated union annotations require at least two members")

    def render(self) -> str:
        return " | ".join(member.render() for member in self.members)

    def imports(self) -> frozenset[Import]:
        imports: set[Import] = set()
        for member in self.members:
            imports.update(member.imports())
        return frozenset(imports)


@dataclass(frozen=True, slots=True)
class TypeIdentity:
    """A database type's stable namespace-qualified identity."""

    schema: str | None
    name: str


@dataclass(frozen=True, slots=True)
class BuiltinType:
    """A dialect builtin or SQLite declared type."""

    name: str


@dataclass(frozen=True, slots=True)
class NamedType:
    """A PostgreSQL enum or domain, identified independently of its spelling."""

    kind: Literal["enum", "domain"]
    identity: TypeIdentity


@dataclass(frozen=True, slots=True)
class ArrayType:
    """A PostgreSQL array whose element retains its complete type identity."""

    element: SqlType


type SqlType = BuiltinType | NamedType | ArrayType


@dataclass(frozen=True, slots=True)
class SchemaColumn:
    name: str
    sql_type: SqlType
    nullable: bool
    primary_key: bool
    has_default: bool = False
    identity: bool = False
    generated: bool = False


@dataclass(frozen=True, slots=True)
class SchemaTable:
    name: str
    columns: tuple[SchemaColumn, ...]
    schema: str | None = None


@dataclass(frozen=True, slots=True)
class SchemaEnum:
    identity: TypeIdentity
    labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DirectType:
    """Use a declared Python annotation and driver decoder for a database type."""

    annotation: RenderExpression
    decoder: DecoderKind


@dataclass(frozen=True, slots=True)
class GeneratedWrapper:
    """Emit a ``NewType`` for a domain whose distinction matters to callers."""

    name: str
    base: DirectType


@dataclass(frozen=True, slots=True)
class RejectedType:
    """Document and reject a type that cannot be represented safely."""

    reason: str


type TypeMapping = DirectType | GeneratedWrapper | RejectedType
type DecoderKind = Literal[
    "bool",
    "bytes",
    "date",
    "datetime",
    "decimal",
    "float",
    "inet",
    "int",
    "json",
    "str",
    "time",
    "uuid",
]


@dataclass(frozen=True, slots=True)
class CodegenConfig:
    """Intentional decisions for types which catalog metadata cannot type safely."""

    type_mappings: Mapping[TypeIdentity, TypeMapping]
