from typing import cast

from relq_migrate.postgres import (
    _Connection,  # pyright: ignore[reportPrivateUsage]
    _history_table_reference,  # pyright: ignore[reportPrivateUsage]
    _is_standard_conforming_strings_statement,  # pyright: ignore[reportPrivateUsage]
    _leading_keywords,  # pyright: ignore[reportPrivateUsage]
    _split_postgres_statements,  # pyright: ignore[reportPrivateUsage]
)


class _FakeConnection:
    def __init__(self, schema_name: str) -> None:
        self.schema_name = schema_name
        self.lookup_count = 0

    def get_server_pid(self) -> int:
        return 12345

    async def fetchval(self, query: str, *args: object) -> str:
        del query, args
        self.lookup_count += 1
        return self.schema_name


async def test_history_schema_cache_is_scoped_to_the_physical_connection() -> None:
    first_connection = _FakeConnection("first_schema")
    second_connection = _FakeConnection("second_schema")

    first_table, first_schema = await _history_table_reference(
        cast(_Connection, first_connection), "relq_migrations"
    )
    second_table, second_schema = await _history_table_reference(
        cast(_Connection, second_connection), "relq_migrations"
    )

    assert (first_table, first_schema) == ('"first_schema"."relq_migrations"', "first_schema")
    assert (second_table, second_schema) == ('"second_schema"."relq_migrations"', "second_schema")
    assert first_connection.lookup_count == 1
    assert second_connection.lookup_count == 1


def test_standard_conforming_strings_detects_quoted_guc_names() -> None:
    assert _is_standard_conforming_strings_statement('SET "standard_conforming_strings" = off')
    assert _is_standard_conforming_strings_statement(
        'SET SESSION "standard_conforming_strings" = off'
    )
    assert tuple(_leading_keywords('SET "standard_conforming_""strings" = off', limit=2)) == (
        "SET",
        'STANDARD_CONFORMING_"STRINGS',
    )


def test_standard_conforming_strings_detects_reset_forms() -> None:
    assert _is_standard_conforming_strings_statement("RESET standard_conforming_strings")
    assert _is_standard_conforming_strings_statement("RESET ALL")
    assert _is_standard_conforming_strings_statement('RESET "standard_conforming_strings"')


def test_splitter_keeps_nested_atomic_body_statements_together() -> None:
    sql = """
    create procedure nested_atomic() language sql begin atomic
        begin
            select 1;
        end;
        select case when true then 1 else 2 end;
    end;
    """

    assert tuple(_split_postgres_statements(sql)) == (sql.strip(),)
