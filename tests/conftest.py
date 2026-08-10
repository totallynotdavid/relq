import asyncio
import sqlite3

from asyncpg import PostgresError

from .postgres_harness import postgres_harness_from_environment, server_version


def pytest_report_header() -> list[str]:
    lines = [f"SQLite {sqlite3.sqlite_version}"]
    harness = postgres_harness_from_environment()
    if harness is None:
        lines.append("PostgreSQL integration: disabled (set RELQ_TEST_POSTGRES_DSN)")
        return lines
    try:
        version = asyncio.run(server_version(harness))
    except PostgresError as error:  # pytest must still report normal connection failures.
        lines.append(f"PostgreSQL integration: unavailable ({error})")
    else:
        lines.append(f"PostgreSQL {version}; owned schema {harness.schema!r}")
    return lines
