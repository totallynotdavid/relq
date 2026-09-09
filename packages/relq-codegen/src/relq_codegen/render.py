"""Deterministic Python source renderer for normalized schema metadata."""

from __future__ import annotations

import keyword
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from relq_codegen.mapping import MAPPING_IMPORTS, relq_name, resolve_type, used_wrapper_policies
from relq_codegen.model import (
    Call,
    CodegenConfig,
    DirectType,
    GeneratedWrapper,
    Import,
    RenderExpression,
    SchemaColumn,
    SchemaEnum,
    SchemaTable,
    TypeIdentity,
)

_MODULE_IMPORTS: frozenset[Import] = frozenset(
    {
        Import("collections.abc", ("Iterable",)),
        Import("dataclasses", ("dataclass",)),
        Import("enum"),
        Import("relq", ("row_adapter",)),
        Import(
            "relq",
            ("Column", "InsertQuery", "Table", "UpdateQuery", "column", "insert_into", "update"),
        ),
        Import("typing", ("Literal", "NewType", "NotRequired", "Required", "TypedDict")),
    }
)
"""Every import this renderer can emit, whether or not a given module needs it.

Reserving the whole set rather than only the imports one module happens to use
keeps generated names stable: adding a JSON column somewhere should not rename
an unrelated class that was already called ``JsonValue``.
"""

_BUILTIN_NAMES: frozenset[str] = frozenset(
    {"bool", "bytes", "float", "int", "list", "str", "tuple"}
)
"""Builtins that appear in rendered annotations, and so must stay unshadowed."""

RELATION_ATTRIBUTES: frozenset[str] = frozenset(
    {
        # Attribute names relq's own relation classes reserve.  relq-codegen
        # deliberately does not depend on relq, so this mirrors the set that
        # ``Source.__init_subclass__`` rejects; the codegen tests import both and
        # assert they agree.
        "_alias",
        "_reference",
        "_schema",
        "_source",
        "as_",
        "column_names",
        "node",
        "output_names",
        "reference",
        "table_name",
    }
)

RESERVED_NAMES: frozenset[str] = (
    frozenset(
        name
        for dependency in _MODULE_IMPORTS | MAPPING_IMPORTS
        for name in dependency.bound_names()
    )
    | _BUILTIN_NAMES
    | RELATION_ATTRIBUTES
)
"""Names a generated module must keep pointing at what it imported.

Python's own ``__dunder__`` namespace is reserved by shape rather than listed
here, in ``_identifier``.

Derived, never hand-listed: the two import sets above are the ones the renderer
and the type mapper actually emit, and both refuse to emit an import they do not
declare.  A configured type mapping adds its own names on top per module -- see
``_reserved_for``.
"""


def _reserved_for(config: CodegenConfig | None) -> frozenset[str]:
    """RESERVED_NAMES plus whatever a caller's type mappings bring into scope."""
    if config is None:
        return RESERVED_NAMES
    expressions = [
        mapping.annotation if isinstance(mapping, DirectType) else mapping.base.annotation
        for mapping in config.type_mappings.values()
        if isinstance(mapping, DirectType | GeneratedWrapper)
    ]
    return RESERVED_NAMES.union(
        *(
            expression.names()
            | frozenset(
                name for dependency in expression.imports() for name in dependency.bound_names()
            )
            for expression in expressions
        )
    )


@dataclass(frozen=True, slots=True)
class _TableNames:
    """Every module-level name one table contributes, allocated as a unit."""

    class_name: str
    instance: str
    columns: tuple[str, ...]

    @property
    def insert_payload(self) -> str:
        return f"{self.class_name}Insert"

    @property
    def update_payload(self) -> str:
        return f"{self.class_name}Update"

    @property
    def row(self) -> str:
        return f"{self.class_name}Row"

    @property
    def insert_function(self) -> str:
        return f"insert_{self.instance}"

    @property
    def update_function(self) -> str:
        return f"update_{self.instance}"

    @property
    def row_adapter(self) -> str:
        return f"{self.instance}_row_adapter"

    def exported(self) -> tuple[str, ...]:
        return (
            self.instance,
            self.insert_payload,
            self.update_payload,
            self.insert_function,
            f"{self.insert_function}_many",
            self.update_function,
            self.row,
            self.row_adapter,
        )


_DUNDER = re.compile(r"__.*__")
"""The reserved shape no generated declaration may take; see ``_identifier``."""

_CLASS_TEMPLATES = ("{}", "{}Insert", "{}Update", "{}Row")
_INSTANCE_TEMPLATES = ("{}", "insert_{}", "insert_{}_many", "update_{}", "{}_row_adapter")


def _allocate(stem: str, templates: tuple[str, ...], taken: set[str]) -> str:
    """Pick the first suffixed form of ``stem`` whose whole name family is free."""
    candidate = stem
    attempt = 1
    while any(template.format(candidate) in taken for template in templates):
        attempt += 1
        candidate = f"{stem}_" if attempt == 2 else f"{stem}_{attempt}"
    taken.update(template.format(candidate) for template in templates)
    return candidate


@dataclass(frozen=True, slots=True)
class _Namespace:
    """The one pass that decides what every generated declaration is called."""

    enums: Mapping[TypeIdentity, str]
    wrappers: frozenset[str]
    tables: tuple[_TableNames, ...]
    reserved: frozenset[str]

    @property
    def protected(self) -> frozenset[str]:
        """Every name a generated declaration must not rebind.

        The imports the module can carry, plus the type names this pass just
        declared, since an annotation refers to those by bare name too.
        """
        return self.reserved | frozenset(self.enums.values()) | self.wrappers

    def check(self, expressions: Iterable[RenderExpression]) -> None:
        """Fail loudly if a rendered expression names something left unreserved.

        Every name a rendered expression resolves through the module namespace
        must be one this pass knew about; otherwise a table or column could have
        been allowed to shadow it.
        """
        unknown = (
            frozenset[str]().union(*(expression.names() for expression in expressions))
            - self.protected
        )
        if unknown:
            raise ValueError(
                "generated source references unreserved name(s): " + ", ".join(sorted(unknown))
            )


def _plan(
    tables: tuple[SchemaTable, ...],
    enums: tuple[SchemaEnum, ...],
    config: CodegenConfig | None,
) -> _Namespace:
    """Allocate every generated name once, against one derived reserved set."""
    reserved = _reserved_for(config)
    taken = set(reserved)
    enum_names = {
        identity: _allocate(stem, ("{}",), taken) for identity, stem in _enum_stems(enums).items()
    }
    wrappers: set[str] = set()
    for policy in used_wrapper_policies(tables, config):
        # A wrapper name is the caller's own choice, so it is rejected rather
        # than renamed: silently generating a different public name than the one
        # configured would be worse than refusing to generate.
        if policy.name in taken:
            raise ValueError(
                f"generated wrapper name collides with a name the generated module "
                f"already binds: {policy.name}"
            )
        taken.add(policy.name)
        wrappers.add(policy.name)
    namespace = _Namespace(enum_names, frozenset(wrappers), (), reserved)
    allocated: list[_TableNames] = []
    for table in tables:
        class_name = _allocate(_class_stem(table.name), _CLASS_TEMPLATES, taken)
        instance = _allocate(_identifier(table.name), _INSTANCE_TEMPLATES, taken)
        # Column attributes live in a class body, where they shadow only the
        # names that body itself resolves -- the imports it calls and the type
        # names its annotations mention -- not the module-level names allocated
        # above, which no class body refers to.
        columns_taken = set(namespace.protected)
        allocated.append(
            _TableNames(
                class_name,
                instance,
                tuple(
                    _allocate(_identifier(field.name), ("{}",), columns_taken)
                    for field in table.columns
                ),
            )
        )
    return replace(namespace, tables=tuple(allocated))


def render(
    tables: tuple[SchemaTable, ...],
    *,
    enums: tuple[SchemaEnum, ...] = (),
    config: CodegenConfig | None = None,
) -> str:
    """Render deterministic Python source from already-normalized metadata."""
    names = _plan(tables, enums, config)
    imports = {
        Import("collections.abc", ("Iterable",)),
        Import("typing", ("Literal", "NotRequired", "Required", "TypedDict")),
        Import(
            "relq",
            ("Column", "InsertQuery", "Table", "UpdateQuery", "column", "insert_into", "update"),
        ),
    }
    body: list[str] = ['"""Generated by relq-codegen; do not edit manually."""', ""]
    exported: list[str] = []
    enum_types = dict(names.enums)
    if enums:
        imports.add(Import("enum"))
    for database_enum in enums:
        class_name = names.enums[database_enum.identity]
        exported.append(class_name)
        body.append(f"class {class_name}(enum.StrEnum):")
        used_names: set[str] = set()
        for index, label in enumerate(database_enum.labels):
            member = _enum_member_name(label, index, used_names)
            used_names.add(member)
            body.append(f"    {member} = {label!r}")
        body.append("")

    policies = used_wrapper_policies(tables, config)
    if policies:
        imports.add(Import("typing", ("NewType",)))
    for policy in policies:
        imports.update(policy.base.annotation.imports())
        exported.append(policy.name)
        body.extend(
            (f"{policy.name} = NewType({policy.name!r}, {policy.base.annotation.render()})", "")
        )

    rendered: list[RenderExpression] = [policy.base.annotation for policy in policies]
    for table, table_names in zip(tables, names.tables, strict=True):
        class_name = table_names.class_name
        exported.append(class_name)
        body.append(f"class {class_name}(Table):")
        if not table.columns:
            body.append("    pass")
        for field, attribute in zip(table.columns, table_names.columns, strict=True):
            resolved = resolve_type(field.sql_type, enum_types, config)
            imports.update(resolved.imports())
            rendered.append(resolved.annotation)
            annotation = (
                f"{resolved.annotation.render()} | None"
                if field.nullable
                else resolved.annotation.render()
            )
            builder = "column" if resolved.builder is None else resolved.builder.render()
            arguments = [] if resolved.builder is not None else [resolved.annotation.render()]
            if attribute != field.name:
                arguments.append(f"name={field.name!r}")
            body.append(
                f"    {attribute}: Column[{annotation}] = {builder}(" + ", ".join(arguments) + ")"
            )
        body.append("")
        instance_name = table_names.instance
        insert_payload = table_names.insert_payload
        update_payload = table_names.update_payload
        insert_function = table_names.insert_function
        update_function = table_names.update_function
        exported.extend(table_names.exported())
        # An introspected schema is part of the table's identity: dropping it
        # would generate a module that silently resolves through search_path
        # instead of the namespace it was read from.
        declaration = repr(table.name)
        if table.schema is not None:
            declaration += f", schema={table.schema!r}"
        body.extend((f"{instance_name} = {class_name}({declaration})", ""))
        _render_payload(
            body, insert_payload, table.columns, kind="insert", enum_types=enum_types, config=config
        )
        body.append("")
        _render_payload(
            body, update_payload, table.columns, kind="update", enum_types=enum_types, config=config
        )
        body.extend(
            (
                "",
                f"def {insert_function}(values: {insert_payload}) -> InsertQuery[tuple[()], Literal[False]]:",
                f"    return insert_into({instance_name}).values(**values)",
                "",
                f"def {insert_function}_many(values: Iterable[{insert_payload}]) -> InsertQuery[tuple[()], Literal[False]]:",
                f"    return insert_into({instance_name}).values_many(values)",
                "",
                f"def {update_function}(values: {update_payload}) -> UpdateQuery[tuple[()], Literal[False], Literal[False]]:",
                f"    return update({instance_name}).values(**values)",
                "",
            )
        )
        imports.update(
            _render_row_model(
                body, table, table_names, enum_types=enum_types, config=config, rendered=rendered
            )
        )
    names.check(rendered)
    if not frozenset(exported).isdisjoint(
        name for dependency in imports for name in dependency.bound_names()
    ):
        raise ValueError(  # pragma: no cover - _plan allocates around every import
            "generated module binds a name it also imports"
        )
    body.insert(1, "\n".join(item.render() for item in sorted(imports, key=Import.render)))
    body.append("__all__ = [" + ", ".join(repr(name) for name in exported) + "]")
    return "\n".join(body) + "\n"


def _enum_stems(enums: tuple[SchemaEnum, ...]) -> Mapping[TypeIdentity, str]:
    counts: dict[str, int] = {}
    for database_enum in enums:
        stem = _class_stem(database_enum.identity.name)
        counts[stem] = counts.get(stem, 0) + 1
    return {
        database_enum.identity: (
            _class_stem(f"{database_enum.identity.schema}_{database_enum.identity.name}")
            if counts[_class_stem(database_enum.identity.name)] > 1
            else _class_stem(database_enum.identity.name)
        )
        for database_enum in enums
    }


def _render_payload(
    body: list[str],
    name: str,
    columns: tuple[SchemaColumn, ...],
    *,
    kind: str,
    enum_types: Mapping[TypeIdentity, str],
    config: CodegenConfig | None,
) -> None:
    payload_columns = tuple(column for column in columns if not column.generated)
    if not all(
        column.name.isidentifier()
        and not keyword.iskeyword(column.name)
        and not _DUNDER.fullmatch(column.name)
        for column in payload_columns
    ):
        fields = ", ".join(
            f"{column.name!r}: {_payload_field_annotation(column, kind, enum_types, config)}"
            for column in payload_columns
        )
        body.append(f"{name} = TypedDict({name!r}, {{{fields}}})")
        return
    body.append(f"class {name}(TypedDict):")
    if not payload_columns:
        body.append("    pass")
        return
    for field in payload_columns:
        body.append(
            f"    {field.name}: {_payload_field_annotation(field, kind, enum_types, config)}"
        )


def _payload_field_annotation(
    field: SchemaColumn,
    kind: str,
    enum_types: Mapping[TypeIdentity, str],
    config: CodegenConfig | None,
) -> str:
    resolved = resolve_type(field.sql_type, enum_types, config)
    annotation = (
        f"{resolved.annotation.render()} | None" if field.nullable else resolved.annotation.render()
    )
    if kind == "insert" and not (
        field.nullable or field.primary_key or field.has_default or field.identity
    ):
        return f"Required[{annotation}]"
    return f"NotRequired[{annotation}]"


def _render_row_model(
    body: list[str],
    table: SchemaTable,
    names: _TableNames,
    *,
    enum_types: Mapping[TypeIdentity, str],
    config: CodegenConfig | None,
    rendered: list[RenderExpression],
) -> set[Import]:
    body.extend(("", "@dataclass(frozen=True, slots=True)", f"class {names.row}:"))
    if not table.columns:
        body.append("    pass")
    decoders: list[RenderExpression | None] = []
    for field, attribute in zip(table.columns, names.columns, strict=True):
        resolved = resolve_type(field.sql_type, enum_types, config)
        annotation = (
            f"{resolved.annotation.render()} | None"
            if field.nullable
            else resolved.annotation.render()
        )
        body.append(f"    {attribute}: {annotation}")
        decoder = resolved.decoder
        decoders.append(
            Call(relq_name("nullable"), (decoder,)) if field.nullable and decoder else decoder
        )
    decoder_tuple = (
        "("
        + ", ".join(decoder.render() if decoder is not None else "None" for decoder in decoders)
        + ("," if len(decoders) == 1 else "")
        + ")"
    )
    body.extend(
        (
            "",
            f"{names.row_adapter} = row_adapter(",
            f"    {names.row},",
            f"    decoders={decoder_tuple},",
            ")",
        )
    )
    imports = {Import("dataclasses", ("dataclass",)), Import("relq", ("row_adapter",))}
    for decoder in decoders:
        if decoder is not None:
            rendered.append(decoder)
            imports.update(decoder.imports())
    return imports


def _class_stem(name: str) -> str:
    result = "".join(part.capitalize() for part in re.split(r"[^0-9A-Za-z]+", name) if part)
    if not result or result[0].isdigit():
        return "Table_" + (result or "Generated")
    return result


def _identifier(name: str) -> str:
    """Normalize a SQL name into an attribute Python lets the module keep.

    Python owns the whole ``__dunder__`` namespace, so a name shaped like one is
    never safe no matter what else is reserved: a table called ``__all__`` is
    overwritten by the module's own export list, and a column called
    ``__annotations__`` is collected as a dataclass field with a dict default.
    Dropping the leading underscores leaves a plain attribute, and unlike a
    suffix it cannot stay dunder-shaped or turn into a private-mangled name.
    """
    result = re.sub(r"\W", "_", name)
    if _DUNDER.fullmatch(result):
        result = result.lstrip("_")
    if not result or result[0].isdigit():
        result = "column_" + result
    if keyword.iskeyword(result) or keyword.issoftkeyword(result):
        result += "_"
    return result


def _enum_member_name(label: str, index: int, used: set[str]) -> str:
    candidate = re.sub(r"\W", "_", label).upper().strip("_") or f"VALUE_{index + 1}"
    if candidate[0].isdigit():
        candidate = "VALUE_" + candidate
    if keyword.iskeyword(candidate.lower()):
        candidate += "_"
    base = candidate
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate
