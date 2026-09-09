"""Resolve normalized SQL types into deliberate Python policies."""

from __future__ import annotations

import keyword
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast, get_args

from relq_codegen.model import (
    ArrayType,
    Attribute,
    BuiltinType,
    Call,
    CodegenConfig,
    DecoderKind,
    DirectType,
    GeneratedWrapper,
    Import,
    Name,
    NamedType,
    RejectedType,
    RenderExpression,
    SchemaTable,
    SqlType,
    StringLiteral,
    Subscript,
    TypeIdentity,
    Union,
)

_DECODER_KINDS: tuple[str, ...] = cast(
    tuple[str, ...], get_args(cast(object, DecoderKind.__value__))
)

RELQ_NAMES: frozenset[str] = frozenset(
    {
        # Every name this module can import into a generated module.  relq_name
        # refuses anything absent here, so the reserved-name pass downstream can
        # never fall behind a newly emitted import.  The decoder half is derived
        # from DecoderKind rather than spelled out.
        *(f"{kind}_decoder" for kind in _DECODER_KINDS),
        "JsonValue",
        "domain_decoder",
        "enum_decoder",
        "json_column",
        "list_decoder",
        "nullable",
        "row_adapter",
    }
)

STDLIB_MODULES: frozenset[str] = frozenset({"datetime", "decimal", "ipaddress", "uuid"})

MAPPING_IMPORTS: frozenset[Import] = frozenset(
    {*(Import("relq", (name,)) for name in RELQ_NAMES), *(Import(m) for m in STDLIB_MODULES)}
)


def _display_identity(identity: TypeIdentity) -> str:
    return identity.name if identity.schema is None else f"{identity.schema}.{identity.name}"


@dataclass(frozen=True, slots=True)
class ResolvedType:
    annotation: RenderExpression
    decoder: RenderExpression | None = None
    builder: Name | None = None
    """A dedicated ``Column`` builder for annotations ``column()`` cannot bind.

    ``column(T)`` infers its result from the ``TypeForm`` value it receives,
    which a type checker will not accept for a recursive type alias such as
    ``JsonValue``.  Such a type names its own builder, which takes the column
    name but no type argument.
    """

    def imports(self) -> frozenset[Import]:
        found = self.annotation.imports()
        if self.decoder is not None:
            found |= self.decoder.imports()
        if self.builder is not None:
            found |= self.builder.imports()
        return found


def resolve_type(
    sql_type: SqlType, enum_types: Mapping[TypeIdentity, str], config: CodegenConfig | None
) -> ResolvedType:
    if isinstance(sql_type, ArrayType):
        element = resolve_type(sql_type.element, enum_types, config)
        decoder = (
            Call(relq_name("list_decoder"), (element.decoder,))
            if element.decoder is not None
            else None
        )
        return ResolvedType(
            Subscript(Name("list"), (element.annotation,)),
            decoder,
        )
    if isinstance(sql_type, NamedType):
        if sql_type.kind == "enum" and (name := enum_types.get(sql_type.identity)) is not None:
            return ResolvedType(
                Name(name),
                Call(relq_name("enum_decoder"), (Name(name),)),
            )
        return _resolve_mapped_type(sql_type.identity, config)
    return _resolve_builtin(sql_type, config)


def _resolve_mapped_type(identity: TypeIdentity, config: CodegenConfig | None) -> ResolvedType:
    mapping = config.type_mappings.get(identity) if config is not None else None
    if isinstance(mapping, DirectType):
        return ResolvedType(_direct_annotation(mapping), _decoder_for_direct_type(mapping))
    if isinstance(mapping, GeneratedWrapper):
        decoder = _decoder_for_direct_type(mapping.base)
        return ResolvedType(
            Name(mapping.name),
            Call(
                relq_name("domain_decoder"),
                (StringLiteral(mapping.name), decoder, Name(mapping.name)),
            ),
        )
    if isinstance(mapping, RejectedType):
        raise TypeError(
            f"database type {_display_identity(identity)} is rejected: {mapping.reason}"
        )
    raise ValueError(
        f"unsupported database type {_display_identity(identity)}; add an explicit relq-codegen mapping"
    )


def _resolve_builtin(sql_type: BuiltinType, config: CodegenConfig | None) -> ResolvedType:
    normalized = sql_type.name.lower()
    mapping = (
        config.type_mappings.get(TypeIdentity(None, normalized)) if config is not None else None
    )
    if mapping is not None:
        if isinstance(mapping, DirectType):
            return ResolvedType(_direct_annotation(mapping), _decoder_for_direct_type(mapping))
        if isinstance(mapping, GeneratedWrapper):
            decoder = _decoder_for_direct_type(mapping.base)
            return ResolvedType(
                Name(mapping.name),
                Call(
                    relq_name("domain_decoder"),
                    (StringLiteral(mapping.name), decoder, Name(mapping.name)),
                ),
            )
        raise TypeError(f"database type {sql_type.name!r} is rejected: {mapping.reason}")
    if "uuid" in normalized:
        return ResolvedType(_attribute("uuid", "UUID"), Call(relq_name("uuid_decoder")))
    if normalized == "inet":
        return ResolvedType(
            Union(
                (
                    _attribute("ipaddress", "IPv4Address"),
                    _attribute("ipaddress", "IPv6Address"),
                    _attribute("ipaddress", "IPv4Interface"),
                    _attribute("ipaddress", "IPv6Interface"),
                )
            ),
            Call(relq_name("inet_decoder")),
        )
    if "json" in normalized:
        return ResolvedType(
            relq_name("JsonValue"),
            Call(relq_name("json_decoder")),
            builder=relq_name("json_column"),
        )
    if "bool" in normalized:
        return ResolvedType(Name("bool"), Call(relq_name("bool_decoder")))
    if (
        normalized in {"int2", "int4", "int8", "integer", "smallint", "bigint"}
        or "serial" in normalized
    ):
        return ResolvedType(Name("int"), Call(relq_name("int_decoder")))
    if any(token in normalized for token in ("numeric", "decimal")):
        return ResolvedType(
            _attribute("decimal", "Decimal"),
            Call(relq_name("decimal_decoder")),
        )
    if any(token in normalized for token in ("real", "double", "float")):
        return ResolvedType(Name("float"), Call(relq_name("float_decoder")))
    if any(token in normalized for token in ("blob", "bytea", "binary")):
        return ResolvedType(Name("bytes"), Call(relq_name("bytes_decoder")))
    if "timestamp" in normalized or "datetime" in normalized:
        return ResolvedType(
            _attribute("datetime", "datetime"),
            Call(relq_name("datetime_decoder")),
        )
    if normalized == "date":
        return ResolvedType(_attribute("datetime", "date"), Call(relq_name("date_decoder")))
    if normalized.startswith("time"):
        return ResolvedType(_attribute("datetime", "time"), Call(relq_name("time_decoder")))
    if "interval" in normalized:
        return ResolvedType(_attribute("datetime", "timedelta"))
    if any(token in normalized for token in ("char", "text", "string", "citext", "xml", "name")):
        return ResolvedType(Name("str"), Call(relq_name("str_decoder")))
    raise ValueError(
        f"unsupported database type {sql_type.name!r}; add an explicit relq-codegen mapping"
    )


def _attribute(module: str, name: str) -> Attribute:
    """Reference ``module.name`` from a standard-library module a module imports."""
    if module not in STDLIB_MODULES:
        raise ValueError(
            f"{module!r} is imported into generated modules but is missing from "
            "STDLIB_MODULES, so generated names would not be kept clear of it"
        )
    return Attribute(Name(module, frozenset({Import(module)})), name)


def _direct_annotation(mapping: DirectType) -> RenderExpression:
    return mapping.annotation


def relq_name(name: str) -> Name:
    """Reference a name that generated modules import from ``relq``."""
    if name not in RELQ_NAMES:
        raise ValueError(
            f"{name!r} is imported into generated modules but is missing from "
            "RELQ_NAMES, so generated names would not be kept clear of it"
        )
    return Name(name, frozenset({Import("relq", (name,))}))


def _decoder_for_direct_type(mapping: DirectType) -> RenderExpression:
    return Call(relq_name(f"{mapping.decoder}_decoder"))


def used_wrapper_policies(
    tables: tuple[SchemaTable, ...], config: CodegenConfig | None
) -> tuple[GeneratedWrapper, ...]:
    if config is None:
        return ()
    used = {
        _identity
        for table in tables
        for column in table.columns
        for _identity in _mapping_identities(column.sql_type)
    }
    wrappers = [
        mapping
        for identity, mapping in config.type_mappings.items()
        if identity in used and isinstance(mapping, GeneratedWrapper)
    ]
    names: set[str] = set()
    for wrapper in wrappers:
        if not wrapper.name.isidentifier() or keyword.iskeyword(wrapper.name):
            raise ValueError(f"generated wrapper name must be a valid identifier: {wrapper.name!r}")
        if wrapper.name in names:
            raise ValueError(f"duplicate generated wrapper name: {wrapper.name}")
        names.add(wrapper.name)
    return tuple(sorted(wrappers, key=lambda wrapper: wrapper.name))


def _mapping_identities(sql_type: SqlType) -> tuple[TypeIdentity, ...]:
    if isinstance(sql_type, NamedType):
        return (sql_type.identity,)
    if isinstance(sql_type, ArrayType):
        return _mapping_identities(sql_type.element)
    return (TypeIdentity(None, sql_type.name.lower()),)
