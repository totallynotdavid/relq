"""Fixtures that own the PostgreSQL test namespace and per-test catalog."""

from collections.abc import AsyncGenerator

import asyncpg
import pytest_asyncio
from relq_postgres import PostgresDatabase

from tests.integration.postgres.support import configured_harness
from tests.postgres_harness import connect_admin, drop_schema, reset_schema, schema_variant


@pytest_asyncio.fixture(scope="session")
async def postgres_schema() -> AsyncGenerator[str]:
    harness = configured_harness()
    await reset_schema(harness)
    await reset_schema(schema_variant(harness, "codegen"))
    await reset_schema(schema_variant(harness, "queue"))
    yield harness.schema
    if not harness.keep_schema:
        await drop_schema(harness)
        await drop_schema(schema_variant(harness, "codegen"))
        await drop_schema(schema_variant(harness, "queue"))


@pytest_asyncio.fixture
async def postgres_admin(postgres_schema: str) -> AsyncGenerator[asyncpg.Connection]:
    harness = configured_harness()
    await reset_schema(harness)
    connection = await connect_admin(harness)
    try:
        yield connection
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def postgres_codegen_schema(postgres_schema: str) -> str:
    harness = schema_variant(configured_harness(), "codegen")
    await reset_schema(harness)
    return harness.schema


@pytest_asyncio.fixture
async def postgres_queue_schema(postgres_schema: str) -> str:
    """Own a second schema, so cross-schema queries have two real namespaces."""
    harness = schema_variant(configured_harness(), "queue")
    await reset_schema(harness)
    return harness.schema


@pytest_asyncio.fixture
async def postgres_codegen_admin(
    postgres_codegen_schema: str,
) -> AsyncGenerator[asyncpg.Connection]:
    connection = await connect_admin(schema_variant(configured_harness(), "codegen"))
    try:
        yield connection
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def database(postgres_admin: asyncpg.Connection) -> PostgresDatabase:
    await postgres_admin.execute(
        "create type relq_integration_state as enum ('new', 'in-progress', 'done')"
    )
    await postgres_admin.execute("""create table relq_integration_users (
        id integer generated always as identity primary key, manager_id integer, name text not null unique,
        active boolean not null default true, state relq_integration_state not null default 'new',
        state_history relq_integration_state[] not null default array['new']::relq_integration_state[],
        name_length integer generated always as (length(name)) stored
    )""")
    await postgres_admin.execute(
        "create table relq_integration_archive (id integer primary key, name text not null)"
    )
    await postgres_admin.execute(
        "create domain relq_integration_tenant_id as integer check (value > 0)"
    )
    await postgres_admin.execute("""create table relq_codegen_values (
        id bigint generated always as identity primary key, tenant relq_integration_tenant_id not null,
        identifier uuid, states relq_integration_state[] not null default array['new']::relq_integration_state[],
        price numeric(12, 2) not null default 0, occurred_at timestamp with time zone, due_time time,
        payload jsonb, origin inet, computed integer generated always as (length(payload::text)) stored
    )""")
    return PostgresDatabase(postgres_admin)
