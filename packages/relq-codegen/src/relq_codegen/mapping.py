"""Resolve normalized SQL types into deliberate Python policies."""

from __future__ import annotations

import keyword
from collections.abc import Mapping
from dataclasses import dataclass

from relq_codegen.model import (
    ArrayType,
    Attribute,
    BuiltinType,
    Call,
    CodegenConfig,
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


def _display_identity(identity: TypeIdentity) -> str:
    return identity.name if identity.schema is None else f"{identity.schema}.{identity.name}"


@dataclass(frozen=True, slots=True)
class ResolvedType:
    annotation: RenderExpression
    decoder: RenderExpression | None = None

    def imports(self) -> frozenset[Import]:
        return self.annotation.imports().union(
            frozenset() if self.decoder is None else self.decoder.imports()
        )


def resolve_type(
    sql_type: SqlType, enum_types: Mapping[TypeIdentity, str], config: CodegenConfig | None
) -> ResolvedType:
    if isinstance(sql_type, ArrayType):
        element = resolve_type(sql_type.element, enum_types, config)
        decoder = (
            Call(relq_decoder("list_decoder"), (element.decoder,))
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
                Call(relq_decoder("enum_decoder"), (Name(name),)),
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
                relq_decoder("domain_decoder"),
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
                    relq_decoder("domain_decoder"),
                    (StringLiteral(mapping.name), decoder, Name(mapping.name)),
                ),
            )
        raise TypeError(f"database type {sql_type.name!r} is rejected: {mapping.reason}")
    if "uuid" in normalized:
        return ResolvedType(
            _attribute("uuid", "UUID", Import("uuid")), Call(relq_decoder("uuid_decoder"))
        )
    if normalized == "inet":
        return ResolvedType(
            Union(
                (
                    _attribute("ipaddress", "IPv4Address", Import("ipaddress")),
                    _attribute("ipaddress", "IPv6Address", Import("ipaddress")),
                    _attribute("ipaddress", "IPv4Interface", Import("ipaddress")),
                    _attribute("ipaddress", "IPv6Interface", Import("ipaddress")),
                )
            ),
            Call(relq_decoder("inet_decoder")),
        )
    if "json" in normalized:
        return ResolvedType(Name("object"), Call(relq_decoder("json_decoder")))
    if "bool" in normalized:
        return ResolvedType(Name("bool"), Call(relq_decoder("bool_decoder")))
    if (
        normalized in {"int2", "int4", "int8", "integer", "smallint", "bigint"}
        or "serial" in normalized
    ):
        return ResolvedType(Name("int"), Call(relq_decoder("int_decoder")))
    if any(token in normalized for token in ("numeric", "decimal")):
        return ResolvedType(
            _attribute("decimal", "Decimal", Import("decimal")),
            Call(relq_decoder("decimal_decoder")),
        )
    if any(token in normalized for token in ("real", "double", "float")):
        return ResolvedType(Name("float"), Call(relq_decoder("float_decoder")))
    if any(token in normalized for token in ("blob", "bytea", "binary")):
        return ResolvedType(Name("bytes"), Call(relq_decoder("bytes_decoder")))
    if "timestamp" in normalized or "datetime" in normalized:
        return ResolvedType(
            _attribute("datetime", "datetime", Import("datetime")),
            Call(relq_decoder("datetime_decoder")),
        )
    if normalized == "date":
        return ResolvedType(
            _attribute("datetime", "date", Import("datetime")), Call(relq_decoder("date_decoder"))
        )
    if normalized.startswith("time"):
        return ResolvedType(
            _attribute("datetime", "time", Import("datetime")), Call(relq_decoder("time_decoder"))
        )
    if "interval" in normalized:
        return ResolvedType(_attribute("datetime", "timedelta", Import("datetime")))
    if any(token in normalized for token in ("char", "text", "string", "citext", "xml", "name")):
        return ResolvedType(Name("str"), Call(relq_decoder("str_decoder")))
    raise ValueError(
        f"unsupported database type {sql_type.name!r}; add an explicit relq-codegen mapping"
    )


def _attribute(module: str, name: str, dependency: Import) -> Attribute:
    return Attribute(Name(module, frozenset({dependency})), name)


def _direct_annotation(mapping: DirectType) -> RenderExpression:
    return mapping.annotation


def relq_decoder(name: str) -> Name:
    return Name(name, frozenset({Import("relq", (name,))}))


def _decoder_for_direct_type(mapping: DirectType) -> RenderExpression:
    return Call(relq_decoder(f"{mapping.decoder}_decoder"))


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
