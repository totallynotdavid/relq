from relq import insert_into
from relq_sqlite import SQLiteDatabase

from .good import users


def rejects_non_row_dml(database: SQLiteDatabase) -> None:
    database.fetch_all(insert_into(users).values(id=1, email="a@example.com", active=True))
