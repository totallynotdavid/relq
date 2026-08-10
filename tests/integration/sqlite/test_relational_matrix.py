import sqlite3
from typing import overload

from relq import ModelSelectQuery, SelectQuery
from relq_sqlite import SQLiteDatabase

from tests.integration.sqlite.matrix_fixture import prepare_sqlite_matrix
from tests.relational_matrix import assert_relational_matrix


class _AsyncSQLiteDatabase:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    @overload
    async def fetch_all[Row](self, query: SelectQuery[Row]) -> list[Row]: ...

    @overload
    async def fetch_all[Model](self, query: ModelSelectQuery[Model]) -> list[Model]: ...

    async def fetch_all[Row](self, query: SelectQuery[Row] | ModelSelectQuery[Row]) -> list[Row]:
        return self._database.fetch_all(query)


async def test_sqlite_relational_matrix() -> None:
    connection = sqlite3.connect(":memory:")
    prepare_sqlite_matrix(connection)
    await assert_relational_matrix(_AsyncSQLiteDatabase(SQLiteDatabase(connection)))
