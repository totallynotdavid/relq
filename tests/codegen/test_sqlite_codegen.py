"""SQLite introspection, rendering, and generated-source runtime contracts."""

import enum
import sqlite3
from pathlib import Path
from typing import cast

import pytest
from relq import JsonValue, Table

# relq-codegen deliberately does not depend on relq, so it mirrors this set
# rather than importing it; reaching in here is what keeps the copy honest.
from relq.expressions.relations import _RESERVED_ATTRIBUTES  # pyright: ignore[reportPrivateUsage]
from relq_codegen import (
    ArrayType,
    BuiltinType,
    CodegenConfig,
    DirectType,
    GeneratedWrapper,
    Import,
    Name,
    NamedType,
    RejectedType,
    SchemaColumn,
    SchemaEnum,
    SchemaTable,
    TypeIdentity,
    generate_sqlite,
    render,
)
from relq_codegen.mapping import RELQ_NAMES, relq_name
from relq_codegen.render import RELATION_ATTRIBUTES, RESERVED_NAMES


def test_sqlite_codegen_emits_nullable_and_primary_key_types() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        'create table "user-events" (id integer primary key, "display name" text, active boolean)'
    )
    generated = generate_sqlite(connection)
    assert "class UserEvents(Table):" in generated
    assert "id: Column[int] = column(int)" in generated
    assert "display_name: Column[str | None] = column(str, name='display name')" in generated
    namespace: dict[str, object] = {}
    exec(generated, namespace)  # noqa: S102 - generated module must import.
    assert isinstance(namespace["UserEvents"], type)


def test_codegen_distinguishes_required_optional_generated_and_batch_payloads() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("""create table widgets (
        id integer primary key, required_name text not null, defaulted_name text not null default 'new',
        note text, doubled integer generated always as (id * 2) stored
    )""")
    generated = generate_sqlite(connection)
    assert "required_name: Required[str]" in generated
    assert "id: NotRequired[int]" in generated
    assert "defaulted_name: NotRequired[str]" in generated
    assert "note: NotRequired[str | None]" in generated
    assert (
        "doubled:"
        not in generated.split("class WidgetsInsert", 1)[1].split("class WidgetsUpdate", 1)[0]
    )
    assert "def insert_widgets_many(values: Iterable[WidgetsInsert])" in generated


def test_codegen_maps_richer_database_value_types() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "create table typed_values (id uuid, price decimal not null, occurred date, payload json, tags varchar)"
    )
    generated = generate_sqlite(connection)
    assert "import decimal" in generated
    assert "import uuid" in generated
    assert "id: Column[uuid.UUID | None]" in generated
    assert "price: Column[decimal.Decimal]" in generated
    assert "occurred: Column[datetime.date | None]" in generated
    assert "payload: Column[JsonValue | None] = json_column()" in generated


def test_codegen_single_field_row_adapter_remains_a_tuple() -> None:
    generated = render(
        (SchemaTable("single_value", (SchemaColumn("id", BuiltinType("int4"), False, True),)),)
    )
    assert "decoders=(int_decoder(),)" in generated
    namespace: dict[str, object] = {}
    exec(generated, namespace)  # noqa: S102 - generated module must import.


def test_codegen_uses_structured_mapping_annotations_and_imports() -> None:
    table = SchemaTable("events", (SchemaColumn("id", BuiltinType("uuid"), False, True),))
    config = CodegenConfig(
        {
            TypeIdentity(None, "uuid"): DirectType(
                Name("EventId", frozenset({Import("ids", ("EventId",))})), "uuid"
            )
        }
    )
    generated = render((table,), config=config)
    assert "from ids import EventId" in generated
    assert "decoders=(uuid_decoder(),)" in generated


def test_structured_imports_reject_invalid_python_names() -> None:
    with pytest.raises(ValueError, match="import names must be identifiers"):
        Import("ids", ("event-id",))


def test_codegen_generates_enums_and_requires_explicit_domain_mapping() -> None:
    state = TypeIdentity("public", "task_state")
    tenant = TypeIdentity("public", "tenant_id")
    table = SchemaTable(
        "tasks",
        (
            SchemaColumn("state", NamedType("enum", state), False, False),
            SchemaColumn("state_history", ArrayType(NamedType("enum", state)), True, False),
            SchemaColumn("priorities", ArrayType(BuiltinType("int4")), True, False),
            SchemaColumn("tenant", NamedType("domain", tenant), False, False),
        ),
    )
    with pytest.raises(ValueError, match="public.tenant_id"):
        render((table,), enums=(SchemaEnum(state, ("todo", "in-progress")),))
    generated = render(
        (table,),
        enums=(SchemaEnum(state, ("todo", "in-progress")),),
        config=CodegenConfig(
            {tenant: DirectType(Name("TenantId", frozenset({Import("ids", ("TenantId",))})), "int")}
        ),
    )
    assert "class TaskState(enum.StrEnum):" in generated
    assert "IN_PROGRESS = 'in-progress'" in generated
    assert "state: Column[TaskState]" in generated
    assert "state_history: Column[list[TaskState] | None]" in generated
    assert "priorities: Column[list[int] | None]" in generated
    assert "tenant: Column[TenantId]" in generated


def test_codegen_uses_qualified_identities_and_explicit_type_policies() -> None:
    public_status = TypeIdentity("public", "status")
    audit_status = TypeIdentity("audit", "status")
    tenant = TypeIdentity("public", "tenant_id")
    opaque = TypeIdentity("public", "opaque_id")
    table = SchemaTable(
        "events",
        (
            SchemaColumn("status", NamedType("enum", public_status), False, False),
            SchemaColumn("audit_status", NamedType("enum", audit_status), False, False),
            SchemaColumn("tenant", NamedType("domain", tenant), False, False),
            SchemaColumn("opaque", NamedType("domain", opaque), False, False),
        ),
    )
    enums = (SchemaEnum(public_status, ("active",)), SchemaEnum(audit_status, ("recorded",)))
    config = CodegenConfig(
        {
            tenant: GeneratedWrapper("TenantId", DirectType(Name("int"), "int")),
            opaque: RejectedType("the application has not declared its value object"),
        }
    )
    with pytest.raises(TypeError, match="public.opaque_id is rejected"):
        render((table,), enums=enums, config=config)
    generated = render((SchemaTable("events", table.columns[:-1]),), enums=enums, config=config)
    assert "class PublicStatus(enum.StrEnum):" in generated
    assert "class AuditStatus(enum.StrEnum):" in generated
    assert "TenantId = NewType('TenantId', int)" in generated


def test_sqlite_codegen_matches_committed_schema_snapshot() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "create table snapshot_users (id integer primary key, email text not null, created_at timestamp default current_timestamp)"
    )
    assert (
        generate_sqlite(connection)
        == (Path(__file__).parents[1] / "snapshots" / "sqlite_schema.py").read_text()
    )


def test_generated_names_survive_columns_and_tables_that_shadow_relq_builders() -> None:
    """A schema cannot rename relq's helpers out from under the module that uses them."""
    connection = sqlite3.connect(":memory:")
    connection.execute("create table row_adapter (id integer primary key, payload jsonb)")
    connection.execute(
        'create table quirks (id integer primary key, "json_column" text, "column" text, '
        '"_schema" text, "node" text, payload jsonb)'
    )
    generated = generate_sqlite(connection)

    assert "json_column_: Column[str | None] = column(str, name='json_column')" in generated
    assert "_schema_: Column[str | None] = column(str, name='_schema')" in generated
    assert "payload: Column[JsonValue | None] = json_column()" in generated
    namespace: dict[str, object] = {}
    exec(generated, namespace)  # noqa: S102 - generated source is the subject under test.
    quirks = namespace["quirks"]
    assert isinstance(quirks, Table)
    assert quirks.column_names() == {"id", "json_column", "column", "_schema", "node", "payload"}


def test_reserved_names_cover_every_relq_name_a_generated_module_imports() -> None:
    """RESERVED_NAMES must keep pace with what codegen actually emits."""
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "create table wide (id uuid, flag boolean, price decimal, size real, blob_value blob, "
        "occurred date, at_time time, seen timestamp, payload json, label text, count_value integer)"
    )
    generated = generate_sqlite(connection)

    imported = {
        name.strip()
        for line in generated.splitlines()
        if line.startswith("from relq import ")
        for name in line.removeprefix("from relq import ").split(",")
    }
    assert imported
    assert imported <= RESERVED_NAMES


def test_a_table_named_after_an_import_does_not_rebind_it() -> None:
    """A table called json_value must not become the module's ``JsonValue``."""
    connection = sqlite3.connect(":memory:")
    connection.execute("create table json_value (id integer primary key, payload jsonb)")
    generated = generate_sqlite(connection)

    assert "class JsonValue_(Table):" in generated
    assert "json_value = JsonValue_('json_value')" in generated
    namespace: dict[str, object] = {}
    exec(generated, namespace)  # noqa: S102 - generated source is the subject under test.
    assert namespace["JsonValue"] is JsonValue
    row = namespace["JsonValue_Row"]
    assert isinstance(row, type)
    assert row.__annotations__["payload"] == JsonValue | None


def test_two_tables_that_normalize_to_one_class_name_stay_distinct() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table user_note (id integer primary key)")
    connection.execute('create table "user note" (id integer primary key)')
    generated = generate_sqlite(connection)

    namespace: dict[str, object] = {}
    exec(generated, namespace)  # noqa: S102 - generated source is the subject under test.
    first, second = namespace["user_note"], namespace["user_note_"]
    assert isinstance(first, Table)
    assert isinstance(second, Table)
    assert {first.table_name, second.table_name} == {"user_note", "user note"}


def test_a_configured_wrapper_cannot_take_a_name_the_module_imports() -> None:
    config = CodegenConfig(
        type_mappings={
            TypeIdentity(None, "citext"): GeneratedWrapper(
                "json_column", DirectType(Name("str"), "str")
            )
        }
    )
    table = SchemaTable(
        "events",
        (SchemaColumn("tag", BuiltinType("citext"), nullable=False, primary_key=False),),
    )

    with pytest.raises(ValueError, match="json_column"):
        render((table,), config=config)


def test_a_configured_wrapper_cannot_take_a_generated_enum_name() -> None:
    identity = TypeIdentity("public", "status")
    config = CodegenConfig(
        type_mappings={
            TypeIdentity(None, "citext"): GeneratedWrapper("Status", DirectType(Name("str"), "str"))
        }
    )
    table = SchemaTable(
        "events",
        (SchemaColumn("tag", BuiltinType("citext"), nullable=False, primary_key=False),),
    )

    with pytest.raises(ValueError, match="Status"):
        render((table,), enums=(SchemaEnum(identity, ("live",)),), config=config)


def test_a_configured_import_reserves_its_name_against_generated_classes() -> None:
    """A caller's own import is part of the namespace generated names dodge."""
    config = CodegenConfig(
        type_mappings={
            TypeIdentity(None, "integer"): DirectType(
                Name("EventId", frozenset({Import("app.ids", ("EventId",))})), "int"
            )
        }
    )
    table = SchemaTable(
        "event_id",
        (SchemaColumn("id", BuiltinType("integer"), nullable=False, primary_key=True),),
    )

    generated = render((table,), config=config)

    assert "from app.ids import EventId" in generated
    assert "class EventId_(Table):" in generated


def test_codegen_reserves_exactly_the_attributes_relq_relations_claim() -> None:
    """relq-codegen does not depend on relq, so the mirrored set is asserted."""
    assert RELATION_ATTRIBUTES == _RESERVED_ATTRIBUTES  # pyright: ignore[reportPrivateUsage]


def test_an_undeclared_relq_import_is_refused_at_the_helper_that_emits_it() -> None:
    """The reserved set cannot fall behind: emitting an import requires declaring it."""
    assert "json_column" in RELQ_NAMES

    with pytest.raises(ValueError, match="not_a_relq_export"):
        relq_name("not_a_relq_export")


def test_a_table_and_an_enum_that_want_the_same_class_name_stay_distinct() -> None:
    identity = TypeIdentity("public", "status")
    tables = (
        SchemaTable(
            "status",
            (
                SchemaColumn("id", BuiltinType("integer"), nullable=False, primary_key=True),
                SchemaColumn(
                    "state", NamedType("enum", identity), nullable=False, primary_key=False
                ),
            ),
        ),
    )

    generated = render(tables, enums=(SchemaEnum(identity, ("live", "dead")),))

    namespace: dict[str, object] = {}
    exec(generated, namespace)  # noqa: S102 - generated source is the subject under test.
    status_enum, status_table = namespace["Status"], namespace["Status_"]
    assert isinstance(status_enum, type) and issubclass(status_enum, enum.StrEnum)
    assert isinstance(status_table, type) and issubclass(status_table, Table)


@pytest.mark.parametrize("name", ["__all__", "__annotations__", "__dict__", "____"])
def test_a_dunder_shaped_name_never_reaches_generated_code(name: str) -> None:
    """Python owns the __dunder__ namespace, so no declaration may take one."""
    connection = sqlite3.connect(":memory:")
    connection.execute(f'create table "{name}" (id integer primary key, "__annotations__" text)')
    generated = generate_sqlite(connection)

    namespace: dict[str, object] = {}
    exec(generated, namespace)  # noqa: S102 - generated source is the subject under test.
    tables = [value for value in namespace.values() if isinstance(value, Table)]
    assert len(tables) == 1
    assert tables[0].table_name == name
    assert tables[0].column_names() == {"id", "__annotations__"}
    exported = cast(list[str], namespace["__all__"])
    assert exported
    assert not [
        binding for binding in exported if binding.startswith("__") and binding.endswith("__")
    ]
