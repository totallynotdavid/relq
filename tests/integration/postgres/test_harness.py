"""Harness lifecycle contract: only its declared schema may be reset or removed."""

import os

import pytest

from tests.integration.postgres.support import configured_harness
from tests.postgres_harness import (
    connect_admin,
    drop_schema,
    reset_schema,
    schema_exists,
    schema_variant,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RELQ_TEST_POSTGRES_DSN") is None,
    reason="set RELQ_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)


async def test_harness_resets_and_removes_only_its_owned_schema(postgres_schema: str) -> None:
    harness = configured_harness()
    lifecycle = schema_variant(harness, "lifecycle")
    await reset_schema(lifecycle)
    try:
        connection = await connect_admin(lifecycle)
        try:
            await connection.execute("create table deliberately_leaked (id integer primary key)")
        finally:
            await connection.close()
        await reset_schema(lifecycle)
        connection = await connect_admin(lifecycle)
        try:
            assert (
                await connection.fetchval(
                    "select to_regclass($1)", f"{lifecycle.schema}.deliberately_leaked"
                )
                is None
            )
        finally:
            await connection.close()
        await drop_schema(lifecycle)
        assert not await schema_exists(lifecycle)
    finally:
        if not harness.keep_schema:
            await drop_schema(lifecycle)
