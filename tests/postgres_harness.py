"""Owned PostgreSQL integration-test namespace and connection helpers."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import asyncpg

_SCHEMA_PATTERN = re.compile(r"relq_test(?:_[a-z0-9_]+)?")


@dataclass(frozen=True, slots=True)
class PostgresHarness:
    dsn: str
    schema: str
    keep_schema: bool


def postgres_harness_from_environment() -> PostgresHarness | None:
    dsn = os.environ.get("RELQ_TEST_POSTGRES_DSN")
    if dsn is None:
        return None
    schema = os.environ.get("RELQ_TEST_POSTGRES_SCHEMA", "relq_test")
    if _SCHEMA_PATTERN.fullmatch(schema) is None:
        raise ValueError(
            "RELQ_TEST_POSTGRES_SCHEMA must be a relq_test-prefixed lowercase identifier"
        )
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker is not None and worker != "master":
        schema = f"{schema}_{worker}"
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("PYTEST_XDIST_WORKER produced an invalid PostgreSQL test schema")
    return PostgresHarness(dsn, schema, os.environ.get("RELQ_KEEP_TEST_SCHEMA") == "1")


def schema_variant(harness: PostgresHarness, suffix: str) -> PostgresHarness:
    schema = f"{harness.schema}_{suffix}"
    if _SCHEMA_PATTERN.fullmatch(schema) is None:
        raise ValueError("PostgreSQL test schema variant must retain the relq_test prefix")
    return PostgresHarness(harness.dsn, schema, harness.keep_schema)


async def reset_schema(harness: PostgresHarness) -> None:
    """Replace only the schema explicitly owned by the integration harness."""
    connection = await asyncpg.connect(harness.dsn)
    try:
        await connection.execute(f"drop schema if exists {harness.schema} cascade")
        await connection.execute(f"create schema {harness.schema}")
    finally:
        await connection.close()


async def drop_schema(harness: PostgresHarness) -> None:
    connection = await asyncpg.connect(harness.dsn)
    try:
        await connection.execute(f"drop schema if exists {harness.schema} cascade")
    finally:
        await connection.close()


async def schema_exists(harness: PostgresHarness) -> bool:
    """Check lifecycle state through an independent control connection."""
    connection = await asyncpg.connect(harness.dsn)
    try:
        return (
            await connection.fetchval(
                "select exists(select 1 from pg_namespace where nspname = $1)", harness.schema
            )
            is True
        )
    finally:
        await connection.close()


async def connect_admin(harness: PostgresHarness) -> asyncpg.Connection:
    """Open a test-only connection whose unqualified names use the harness schema."""
    connection = await asyncpg.connect(harness.dsn)
    await connection.execute(
        "select set_config('search_path', $1, false)", f"{harness.schema}, public"
    )
    return connection


async def server_version(harness: PostgresHarness) -> str:
    connection = await asyncpg.connect(harness.dsn)
    try:
        return str(connection.get_server_version())
    finally:
        await connection.close()
