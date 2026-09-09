"""PostgreSQL catalog introspection and regenerated-module contracts."""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
import pytest
from relq import select_model
from relq_codegen import (
    CodegenConfig,
    DirectType,
    GeneratedWrapper,
    Name,
    TypeIdentity,
    generate_postgres,
)
from relq_codegen.__main__ import main as codegen_main
from relq_postgres import PostgresDatabase

from tests.integration.postgres.support import configured_harness

pytestmark = pytest.mark.skipif(
    os.environ.get("RELQ_TEST_POSTGRES_DSN") is None,
    reason="set RELQ_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)


def _direct_mapping(schema: str) -> CodegenConfig:
    return CodegenConfig(
        {TypeIdentity(schema, "relq_integration_tenant_id"): DirectType(Name("int"), "int")}
    )


def _wrapped_mapping(schema: str) -> CodegenConfig:
    return CodegenConfig(
        {
            TypeIdentity(schema, "relq_integration_tenant_id"): GeneratedWrapper(
                "TenantId", DirectType(Name("int"), "int")
            )
        }
    )


async def test_codegen_preserves_type_identity_and_generated_batch_helpers(
    database: PostgresDatabase, postgres_admin: asyncpg.Connection, postgres_schema: str
) -> None:
    generated = await generate_postgres(
        postgres_admin, schema=postgres_schema, config=_wrapped_mapping(postgres_schema)
    )
    assert generated == await generate_postgres(
        postgres_admin, schema=postgres_schema, config=_wrapped_mapping(postgres_schema)
    )
    assert "TenantId = NewType('TenantId', int)" in generated
    assert "states: Column[list[RelqIntegrationState]]" in generated
    assert "payload: Column[JsonValue | None] = json_column()" in generated
    assert (
        "origin: Column[ipaddress.IPv4Address | ipaddress.IPv6Address | ipaddress.IPv4Interface | ipaddress.IPv6Interface | None]"
        in generated
    )
    namespace: dict[str, object] = {"select_model": select_model}
    exec(generated, namespace)  # noqa: S102 - generated source is the subject under test.
    assert "def insert_relq_codegen_values_many" in generated


async def test_codegen_matches_committed_catalog_snapshot(
    database: PostgresDatabase, postgres_admin: asyncpg.Connection, postgres_schema: str
) -> None:
    # Generated tables now carry the schema they were introspected from, and
    # the harness owns a per-run schema name, so the snapshot is committed with
    # the default one and rebound to whichever schema this run owns.
    snapshot = (Path(__file__).parents[2] / "snapshots" / "postgres_schema.py").read_text()
    assert await generate_postgres(
        postgres_admin, schema=postgres_schema, config=_direct_mapping(postgres_schema)
    ) == snapshot.replace("schema='relq_test'", f"schema={postgres_schema!r}")


async def test_codegen_cli_detects_changed_schema(
    postgres_codegen_admin: asyncpg.Connection,
    postgres_codegen_schema: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await postgres_codegen_admin.execute(
        "create table values (id integer primary key, payload jsonb)"
    )
    output = tmp_path / "schema.py"
    arguments = [
        "relq-codegen",
        "postgres",
        configured_harness().dsn,
        str(output),
        "--schema",
        postgres_codegen_schema,
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    await asyncio.to_thread(codegen_main)
    monkeypatch.setattr(sys, "argv", [*arguments, "--check"])
    await asyncio.to_thread(codegen_main)
    await postgres_codegen_admin.execute("alter table values add column cli_probe text")
    with pytest.raises(SystemExit, match="stale"):
        await asyncio.to_thread(codegen_main)


async def test_codegen_changed_schema_regeneration_is_focused(
    database: PostgresDatabase, postgres_admin: asyncpg.Connection, postgres_schema: str
) -> None:
    config = _wrapped_mapping(postgres_schema)
    before = await generate_postgres(postgres_admin, schema=postgres_schema, config=config)
    await postgres_admin.execute("alter table relq_codegen_values add column metadata jsonb")
    after = await generate_postgres(postgres_admin, schema=postgres_schema, config=config)
    assert after != before
    assert "metadata: Column[JsonValue | None] = json_column()" in after
    assert "metadata: NotRequired[JsonValue | None]" in after
