import sqlite3

import pytest
from relq import (
    Column,
    DerivedTable,
    Table,
    WindowExclusion,
    add,
    column,
    count,
    cume_dist,
    current_row,
    dense_rank,
    exists,
    following,
    output_column,
    percent_rank,
    preceding,
    rank,
    row_number,
    select,
    sum,
    unbounded_preceding,
)
from relq._compiler import CompiledQuery, compile_postgres, compile_sqlite
from relq._node_value import node_of
from relq_sqlite import SQLiteDatabase

from tests.compiler_fixtures import assert_compiles


class Users(Table):
    id: Column[int] = column(int)
    account_id: Column[int] = column(int)
    score: Column[int] = column(int)


users = Users("users")


class GroupedScores(DerivedTable):
    account_id: Column[int] = output_column(int)
    total: Column[int | None] = output_column(int)


def test_window_sql_contracts_cover_empty_partition_and_ordered_forms() -> None:
    query = (
        select(
            row_number().over().as_("position"),
            count(users.id).over().partition_by(users.account_id).as_("account_size"),
            sum(users.score)
            .over()
            .partition_by(users.account_id)
            .order_by(users.score.desc())
            .as_("running_score"),
        )
        .from_(users)
        .where(users.id.gt(7))
    )
    sqlite_sql = (
        'select row_number() over () as "position", '
        'count("users"."id") over (partition by "users"."account_id") as "account_size", '
        'sum("users"."score") over (partition by "users"."account_id" '
        'order by "users"."score" desc) as "running_score" '
        'from "users" where ("users"."id" > ?)'
    )
    postgres_sql = sqlite_sql.replace("?", "$1")
    assert_compiles(
        query,
        sqlite=CompiledQuery(sqlite_sql, (7,)),
        postgres=CompiledQuery(postgres_sql, (7,)),
    )


def test_aggregate_filter_has_one_portable_sql_and_result_contract() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table users (id integer, account_id integer, score integer)")
    connection.executemany(
        "insert into users values (?, ?, ?)", ((1, 1, 10), (2, 1, 20), (3, 2, 30))
    )
    query = (
        select(
            count().filter(users.account_id.eq(1)).as_("account_one"),
            sum(users.score).filter(users.score.gt(10)).as_("large_scores"),
            count().filter(users.id.gt(0)).filter(users.score.lt(30)).as_("combined"),
        )
        .from_(users)
        .where(users.id.gt(0))
    )
    sqlite_sql = (
        'select count(*) filter (where ("users"."account_id" = ?)) as "account_one", '
        'sum("users"."score") filter (where ("users"."score" > ?)) as "large_scores", '
        'count(*) filter (where (("users"."id" > ?) and ("users"."score" < ?))) as "combined" '
        'from "users" where ("users"."id" > ?)'
    )
    assert_compiles(
        query,
        sqlite=CompiledQuery(sqlite_sql, (1, 10, 0, 30, 0)),
        postgres=CompiledQuery(
            sqlite_sql.replace("?", "$1", 1)
            .replace("?", "$2", 1)
            .replace("?", "$3", 1)
            .replace("?", "$4", 1)
            .replace("?", "$5", 1),
            (1, 10, 0, 30, 0),
        ),
    )
    assert SQLiteDatabase(connection).fetch_all(query) == [(2, 50, 2)]


def test_window_functions_execute_with_partitioned_ranking() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table users (id integer, account_id integer, score integer)")
    connection.executemany(
        "insert into users values (?, ?, ?)",
        ((1, 1, 10), (2, 1, 30), (3, 2, 20), (4, 2, 20)),
    )
    query = (
        select(
            users.id,
            row_number().over().partition_by(users.account_id).order_by(users.score.desc()),
            rank().over().partition_by(users.account_id).order_by(users.score.desc()),
            dense_rank().over().partition_by(users.account_id).order_by(users.score.desc()),
        )
        .from_(users)
        .order_by(users.id.asc())
    )
    assert SQLiteDatabase(connection).fetch_all(query) == [
        (1, 2, 2, 2),
        (2, 1, 1, 1),
        (3, 1, 1, 1),
        (4, 2, 1, 1),
    ]


def test_explicit_null_ordering_is_shared_by_queries_and_windows() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table users (id integer, account_id integer, score integer)")
    connection.executemany(
        "insert into users values (?, ?, ?)", ((1, 1, None), (2, 1, 1), (3, 1, 2))
    )
    placements = (
        (users.score.asc().nulls_first(), [1, 2, 3]),
        (users.score.asc().nulls_last(), [2, 3, 1]),
        (users.score.desc().nulls_first(), [1, 3, 2]),
        (users.score.desc().nulls_last(), [3, 2, 1]),
    )
    database = SQLiteDatabase(connection)
    for order, ids in placements:
        query = select(users.id).from_(users).order_by(order)
        assert database.fetch_all(query) == [(identifier,) for identifier in ids]
        windowed = (
            select(users.id, row_number().over().order_by(order))
            .from_(users)
            .order_by(users.id.asc())
        )
        assert [ranked_id for _, ranked_id in database.fetch_all(windowed)] == [
            ids.index(identifier) + 1 for identifier in (1, 2, 3)
        ]
        assert f"nulls {node_of(order).nulls}" in compile_sqlite(query).sql
        assert compile_postgres(query) == compile_sqlite(query)
    with pytest.raises(ValueError, match="only one NULL placement"):
        users.score.asc().nulls_first().nulls_last()


def test_distribution_window_functions_have_one_portable_peer_contract() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table users (id integer, account_id integer, score integer)")
    connection.executemany(
        "insert into users values (?, ?, ?)",
        ((1, 1, None), (2, 1, 10), (3, 1, 10), (4, 1, 20), (5, 2, 7)),
    )
    query = (
        select(
            users.id,
            percent_rank()
            .over()
            .partition_by(users.account_id)
            .order_by(users.score.asc().nulls_last()),
            cume_dist()
            .over()
            .partition_by(users.account_id)
            .order_by(users.score.asc().nulls_last()),
        )
        .from_(users)
        .order_by(users.id.asc())
    )
    sqlite = compile_sqlite(query)
    assert "percent_rank() over" in sqlite.sql
    assert "cume_dist() over" in sqlite.sql
    assert "nulls last" in sqlite.sql
    assert compile_postgres(query) == sqlite
    assert SQLiteDatabase(connection).fetch_all(query) == [
        (1, 1.0, 1.0),
        (2, 0.0, 0.5),
        (3, 0.0, 0.5),
        (4, 2 / 3, 0.75),
        (5, 0.0, 1.0),
    ]


def test_window_frames_compile_and_execute_with_shared_semantics() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table users (id integer, account_id integer, score integer)")
    connection.executemany(
        "insert into users values (?, ?, ?)", ((1, 1, 10), (2, 1, 20), (3, 1, 30))
    )
    query = (
        select(
            users.id,
            sum(users.score)
            .over()
            .partition_by(users.account_id)
            .order_by(users.id.asc())
            .rows_between(unbounded_preceding(), current_row()),
            sum(users.score)
            .over()
            .partition_by(users.account_id)
            .order_by(users.id.asc())
            .rows_between(preceding(1), following(1)),
        )
        .from_(users)
        .order_by(users.id.asc())
    )
    compiled = compile_sqlite(query)
    assert "rows between unbounded preceding and current row" in compiled.sql
    assert compile_postgres(query).sql == compiled.sql
    assert SQLiteDatabase(connection).fetch_all(query) == [(1, 10, 30), (2, 30, 60), (3, 60, 50)]


def test_window_exclusions_have_a_closed_portable_sql_and_peer_contract() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("create table users (id integer, account_id integer, score integer)")
    connection.executemany(
        "insert into users values (?, ?, ?)",
        ((1, 1, 10), (2, 1, 10), (3, 1, 20), (4, 1, 20), (5, 1, 30)),
    )
    base = (
        sum(users.score)
        .over()
        .partition_by(users.account_id)
        .order_by(users.score.asc())
        .groups_between(unbounded_preceding(), current_row())
    )
    query = (
        select(
            users.id,
            base.exclude(WindowExclusion.NO_OTHERS),
            base.exclude(WindowExclusion.CURRENT_ROW),
            base.exclude(WindowExclusion.GROUP),
            base.exclude(WindowExclusion.TIES),
        )
        .from_(users)
        .order_by(users.id.asc())
    )
    sqlite = compile_sqlite(query)
    assert sqlite.sql.count("exclude no others") == 1
    assert sqlite.sql.count("exclude current row") == 1
    assert sqlite.sql.count("exclude group") == 1
    assert sqlite.sql.count("exclude ties") == 1
    assert compile_postgres(query) == sqlite
    assert SQLiteDatabase(connection).fetch_all(query) == [
        (1, 20, 10, None, 10),
        (2, 20, 10, None, 10),
        (3, 60, 40, 20, 40),
        (4, 60, 40, 20, 40),
        (5, 90, 60, 60, 90),
    ]
    with pytest.raises(ValueError, match="require an explicit frame"):
        sum(users.score).over().exclude(WindowExclusion.CURRENT_ROW)
    with pytest.raises(ValueError, match="only one exclusion"):
        base.exclude(WindowExclusion.GROUP).exclude(WindowExclusion.TIES)


def test_analytic_diagnostics_reject_nonportable_invalid_placements() -> None:
    with pytest.raises(ValueError, match="WHERE cannot contain aggregate"):
        compile_sqlite(select(users.id).from_(users).where(count(users.id).gt(1)))
    with pytest.raises(ValueError, match="GROUP BY cannot contain aggregate"):
        compile_sqlite(select(users.id).from_(users).group_by(count(users.id)))
    with pytest.raises(ValueError, match="HAVING cannot contain window"):
        compile_sqlite(select(users.id).from_(users).having(row_number().over().gt(1)))
    with pytest.raises(ValueError, match="RANGE frames with an offset"):
        compile_sqlite(
            select(sum(users.score).over().range_between(preceding(1), current_row())).from_(users)
        )
    with pytest.raises(ValueError, match="start cannot follow its end"):
        sum(users.score).over().rows_between(following(1), current_row())
    with pytest.raises(ValueError, match="aggregate expressions cannot contain another aggregate"):
        compile_sqlite(select(count().filter(count(users.id).gt(0))).from_(users))


def test_grouping_diagnostics_enforce_the_shared_database_contract() -> None:
    valid = (
        select(users.account_id, add(users.account_id, 1), sum(users.score))
        .from_(users)
        .group_by(users.account_id)
        .having(users.account_id.gt(0) & sum(users.score).gt(1))
        .order_by(users.account_id.asc())
    )
    assert 'group by "users"."account_id"' in compile_sqlite(valid).sql

    with pytest.raises(ValueError, match=r"SELECT references ungrouped.*users.score"):
        compile_sqlite(
            select(users.account_id, users.score, sum(users.score))
            .from_(users)
            .group_by(users.account_id)
        )
    with pytest.raises(ValueError, match=r"HAVING references ungrouped.*users.score"):
        compile_sqlite(
            select(users.account_id, count())
            .from_(users)
            .group_by(users.account_id)
            .having(users.score.gt(0))
        )
    with pytest.raises(ValueError, match=r"ORDER BY references ungrouped.*users.score"):
        compile_sqlite(
            select(users.account_id, count())
            .from_(users)
            .group_by(users.account_id)
            .order_by(users.score.asc())
        )
    with pytest.raises(ValueError, match="HAVING requires GROUP BY or an aggregate"):
        compile_sqlite(select(users.id).from_(users).having(users.id.gt(0)))
    with pytest.raises(TypeError, match="GROUP BY expressions cannot be aliased"):
        compile_sqlite(select(users.account_id).from_(users).group_by(users.account_id.as_("id")))

    grouped = (
        select(users.account_id, sum(users.score).as_("total"))
        .from_(users)
        .group_by(users.account_id)
        .as_(GroupedScores, "grouped_scores")
    )
    assert (
        'from (select "users"."account_id", sum("users"."score") as "total"'
        in compile_sqlite(select(grouped.account_id, grouped.total).from_(grouped)).sql
    )


def test_grouping_scopes_treat_correlated_values_as_constants() -> None:
    manager = users.as_("grouping_manager")
    correlated = (
        select(count())
        .from_(manager)
        .where(manager.account_id.eq(users.account_id))
        .having(users.id.gt(0) & count().gt(0))
    )
    # Build the portable correlated form through EXISTS so its nested grouping
    # scope is validated while the outer query remains non-aggregate.
    query = select(users.id).from_(users).where(exists(correlated))
    assert 'having (("users"."id" > ?) and (count(*) > ?))' in compile_sqlite(query).sql


def test_join_methods_and_visibility_contracts() -> None:
    manager = users.as_("manager")
    reviewer = users.as_("reviewer")
    query = (
        select(users.id, manager.id, reviewer.id.nullable())
        .from_(users)
        .inner_join(manager, on=users.account_id.eq(manager.account_id))
        .left_join(reviewer, on=manager.account_id.eq(reviewer.account_id))
    )
    assert 'inner join "users" as "manager"' in compile_sqlite(query).sql
    assert 'left join "users" as "reviewer"' in compile_sqlite(query).sql

    invalid = (
        select(users.id)
        .from_(users)
        .inner_join(manager, on=reviewer.account_id.eq(manager.account_id))
        .inner_join(reviewer, on=users.account_id.eq(reviewer.account_id))
    )
    with pytest.raises(ValueError, match="not visible at this join: reviewer"):
        compile_sqlite(invalid)


def test_cross_right_and_full_join_compile_without_a_generic_kind_parameter() -> None:
    manager = users.as_("manager")
    query = select(users.id).from_(users).cross_join(manager)
    assert 'cross join "users" as "manager"' in compile_sqlite(query).sql
    assert (
        'right join "users" as "manager"'
        in compile_sqlite(
            select(users.id.nullable()).from_(users).right_join(manager, on=users.id.eq(manager.id))
        ).sql
    )
    assert (
        'full join "users" as "manager"'
        in compile_sqlite(
            select(users.id.nullable()).from_(users).full_join(manager, on=users.id.eq(manager.id))
        ).sql
    )
