"""SQLite introspection, rendering, and generated-source runtime contracts."""

import sqlite3
from pathlib import Path

import pytest
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
    assert "payload: Column[object | None]" in generated


def test_codegen_maps_postgres_temporal_catalog_spellings_to_branded_domains() -> None:
    table = SchemaTable(
        "temporal_values",
        (
            SchemaColumn(
                "local_timestamp", BuiltinType("timestamp without time zone"), False, False
            ),
            SchemaColumn("instant", BuiltinType("timestamp with time zone"), False, False),
            SchemaColumn("local_time", BuiltinType("time without time zone"), False, False),
            SchemaColumn("zoned_time", BuiltinType("time with time zone"), False, False),
            SchemaColumn("elapsed", BuiltinType("interval"), False, False),
        ),
    )
    generated = render((table,))
    assert "local_timestamp: Column[NaiveDateTime] = column(NaiveDateTime)" in generated
    assert "instant: Column[AwareDateTime] = column(AwareDateTime)" in generated
    assert "local_time: Column[NaiveTime] = column(NaiveTime)" in generated
    assert "zoned_time: Column[AwareTime] = column(AwareTime)" in generated
    assert "elapsed: Column[Interval] = column(Interval)" in generated
    assert "naive_datetime_decoder()" in generated
    assert "aware_datetime_decoder()" in generated
    assert "naive_time_decoder()" in generated
    assert "aware_time_decoder()" in generated
    assert "interval_decoder()" in generated
    namespace: dict[str, object] = {}
    exec(generated, namespace)  # noqa: S102 - generated module must import.


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
