from typing import Literal, assert_type

from generated_schema import SnapshotUsersInsert, insert_snapshot_users_many
from relq import InsertQuery

first: SnapshotUsersInsert = {"email": "ada@example.com"}
second: SnapshotUsersInsert = {"id": 2, "email": "grace@example.com"}
batch = insert_snapshot_users_many((first, second))

assert_type(batch, InsertQuery[tuple[()], Literal[False]])
