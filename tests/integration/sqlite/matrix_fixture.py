"""Backend-specific setup for the one shared relational behavior matrix."""

import sqlite3

_LEFT_ROWS = ((1,), (2,))
_RIGHT_ROWS = ((2,), (3,))
_THIRD_ROWS = ((3,), (4,))
_PEOPLE_ROWS = ((1, None, True), (2, 1, True), (3, 1, False), (4, 99, True), (5, 2, True))
_DDL = (
    "create table relq_matrix_left (id integer primary key)",
    "create table relq_matrix_right (id integer primary key)",
    "create table relq_matrix_third (id integer primary key)",
    "create table relq_matrix_people (id integer primary key, manager_id integer, active boolean not null)",
    "create table relq_matrix_semantics (id integer primary key, nullable_value integer, numerator integer not null, denominator integer not null, ratio real not null, price numeric not null)",
)
_SEMANTIC_ROWS = ((1, None, 3, 2, 1.5, 1.5), (2, 2, 5, 2, 2.5, 2.5))


def prepare_sqlite_matrix(connection: sqlite3.Connection) -> None:
    for statement in _DDL:
        connection.execute(statement)
    connection.executemany("insert into relq_matrix_left values (?)", _LEFT_ROWS)
    connection.executemany("insert into relq_matrix_right values (?)", _RIGHT_ROWS)
    connection.executemany("insert into relq_matrix_third values (?)", _THIRD_ROWS)
    connection.executemany("insert into relq_matrix_people values (?, ?, ?)", _PEOPLE_ROWS)
    connection.executemany(
        "insert into relq_matrix_semantics values (?, ?, ?, ?, ?, ?)", _SEMANTIC_ROWS
    )
