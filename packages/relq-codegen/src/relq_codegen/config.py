"""Load the type mapping a caller commits next to their schema.

Two spellings reach the same ``CodegenConfig``. A TOML file is the declarative
form::

    [types.citext]
    python = "str"
    decoder = "str"

    [types."public.tenant_id"]
    wrapper = "TenantId"
    python = "int"
    decoder = "int"

    [types.geometry]
    reject = "the application has no value object for it yet"

A Python module (``package.module`` or ``package.module:NAME``) is for mappings
that need an import-qualified ``RenderExpression`` or code to build them; it
exposes a ``CodegenConfig`` as ``CONFIG`` unless the reference names another
attribute.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from relq_codegen.errors import CodegenError
from relq_codegen.mapping import DECODER_KINDS
from relq_codegen.model import (
    Attribute,
    CodegenConfig,
    DecoderKind,
    DirectType,
    GeneratedWrapper,
    Import,
    Name,
    RejectedType,
    RenderExpression,
    Subscript,
    TypeIdentity,
    TypeMapping,
    Union,
)

DEFAULT_ATTRIBUTE = "CONFIG"
_KEYS = frozenset({"python", "decoder", "wrapper", "reject"})


def load_config(reference: str) -> CodegenConfig:
    """Read a ``.toml`` file, or import a module that defines the configuration."""
    if reference.endswith(".toml"):
        return _load_toml(Path(reference))
    return _load_module(reference)


def _load_toml(path: Path) -> CodegenConfig:
    document: dict[str, object]
    try:
        document = tomllib.loads(path.read_text())
    except OSError as error:
        raise CodegenError(f"cannot read config {path}: {error.strerror}") from error
    except tomllib.TOMLDecodeError as error:
        raise CodegenError(f"invalid config {path}: {error}") from error
    unknown = document.keys() - {"types"}
    if unknown:
        raise CodegenError(f"invalid config {path}: unknown table {min(unknown)!r}")
    types = _table(document.get("types", {}))
    if types is None:
        raise CodegenError(f"invalid config {path}: 'types' must be a table")
    mappings: dict[TypeIdentity, TypeMapping] = {}
    for key, value in types.items():
        entry = _table(value)
        if entry is None:
            raise CodegenError(f"invalid config {path}: [types.{key}] must be a table")
        try:
            mappings[_identity(key)] = _mapping(entry)
        except CodegenError as error:
            raise CodegenError(f"invalid config {path}: [types.{key}] {error}") from error
    return CodegenConfig(mappings)


def _table(value: object) -> dict[str, object] | None:
    """TOML tables always have string keys; anything else is not a table."""
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _identity(key: str) -> TypeIdentity:
    schema, dot, name = key.partition(".")
    if not dot:
        return TypeIdentity(None, key.lower())
    if not schema or not name:
        raise CodegenError(f"type name {key!r} must be 'name' or 'schema.name'")
    return TypeIdentity(schema, name)


def _mapping(entry: Mapping[str, object]) -> TypeMapping:
    unknown = entry.keys() - _KEYS
    if unknown:
        raise CodegenError(f"has unknown key {min(unknown)!r}")
    reason = entry.get("reject")
    if reason is not None:
        if len(entry) != 1 or not isinstance(reason, str) or not reason:
            raise CodegenError("'reject' takes a reason string and no other key")
        return RejectedType(reason)
    python = entry.get("python")
    decoder = entry.get("decoder")
    if not isinstance(python, str) or not isinstance(decoder, str):
        raise CodegenError("needs string 'python' and 'decoder' keys, or 'reject'")
    direct = DirectType(_annotation(python), _decoder_kind(decoder))
    wrapper = entry.get("wrapper")
    if wrapper is None:
        return direct
    if not isinstance(wrapper, str):
        raise CodegenError("'wrapper' must be a string")
    return GeneratedWrapper(wrapper, direct)


def _decoder_kind(value: str) -> DecoderKind:
    match value:
        case (
            "bool"
            | "bytes"
            | "date"
            | "datetime"
            | "decimal"
            | "float"
            | "inet"
            | "int"
            | "json"
            | "str"
            | "time"
            | "uuid"
        ):
            return value
        case _:
            raise CodegenError(f"decoder {value!r} is not one of: {', '.join(DECODER_KINDS)}")


def _annotation(source: str) -> RenderExpression:
    """Parse a Python type expression into import-aware render nodes.

    A builtin type is written bare, anything else as ``module.Name``, which also
    names the module to import. A bare non-builtin name is refused because
    nothing says where it comes from.
    """
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as error:
        raise CodegenError(f"'python' is not a Python expression: {source!r}") from error
    return _expression(tree.body, source)


def _expression(node: ast.expr, source: str) -> RenderExpression:
    match node:
        case ast.Name(id=name):
            if not isinstance(getattr(builtins, name, None), type):
                raise CodegenError(
                    f"'python' name {name!r} has no module; write 'module.{name}' in {source!r}"
                )
            return Name(name)
        case ast.Attribute():
            return _qualified(node, source)
        case ast.Subscript(value=parent, slice=ast.Tuple(elts=items)):
            return Subscript(
                _expression(parent, source), tuple(_expression(item, source) for item in items)
            )
        case ast.Subscript(value=parent, slice=item):
            return Subscript(_expression(parent, source), (_expression(item, source),))
        case ast.BinOp(left=left, op=ast.BitOr(), right=right):
            members: list[RenderExpression] = []
            for side in (left, right):
                member = _expression(side, source)
                members.extend(member.members if isinstance(member, Union) else (member,))
            return Union(tuple(members))
        case _:
            raise CodegenError(f"'python' has an unsupported expression: {source!r}")


def _qualified(node: ast.Attribute, source: str) -> RenderExpression:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        raise CodegenError(f"'python' has an unsupported expression: {source!r}")
    parts.append(current.id)
    parts.reverse()
    try:
        module = Import(".".join(parts[:-1]))
    except ValueError as error:
        raise CodegenError(f"'python' has an invalid module in {source!r}") from error
    expression: RenderExpression = Name(parts[0], frozenset({module}))
    for part in parts[1:]:
        expression = Attribute(expression, part)
    return expression


def _load_module(reference: str) -> CodegenConfig:
    module_name, _, attribute = reference.partition(":")
    attribute = attribute or DEFAULT_ATTRIBUTE
    # Console scripts do not put the working directory on the import path.
    sys.path.insert(0, "")
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise CodegenError(f"cannot import config module {module_name!r}: {error}") from error
    finally:
        sys.path.remove("")
    value: object = getattr(module, attribute, None)
    if not isinstance(value, CodegenConfig):
        raise CodegenError(
            f"config module {module_name!r} must define {attribute} as a CodegenConfig"
        )
    return value
