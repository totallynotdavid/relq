"""One portable execution contract for relational composition."""

import decimal
from dataclasses import dataclass
from typing import Protocol, overload

from relq import (
    Column,
    CteTable,
    DerivedTable,
    ModelSelectQuery,
    SelectQuery,
    Table,
    add,
    avg,
    case_when,
    coalesce,
    column,
    count,
    cte,
    decimal_decoder,
    divide,
    exists,
    multiply,
    nullif,
    output_column,
    row_adapter,
    scalar,
    select,
    select_model,
    sum,
)


class MatrixDatabase(Protocol):
    @overload
    async def fetch_all[Row](self, query: SelectQuery[Row]) -> list[Row]: ...

    @overload
    async def fetch_all[Model](self, query: ModelSelectQuery[Model]) -> list[Model]: ...


class MatrixLeft(Table):
    id: Column[int] = column(int)


class MatrixRight(Table):
    id: Column[int] = column(int)


class MatrixThird(Table):
    id: Column[int] = column(int)


class MatrixPeople(Table):
    id: Column[int] = column(int)
    manager_id: Column[int | None] = column(int)
    active: Column[bool] = column(bool)


class MatrixSemantics(Table):
    id: Column[int] = column(int)
    nullable_value: Column[int | None] = column(int)
    numerator: Column[int] = column(int)
    denominator: Column[int] = column(int)
    ratio: Column[float] = column(float)
    price: Column[decimal.Decimal] = column(decimal.Decimal)


class MatrixIds(DerivedTable):
    id: Column[int] = output_column(int)


class MatrixActiveReports(DerivedTable):
    active: Column[bool] = output_column(bool)
    reports: Column[int] = output_column(int)


class MatrixActiveIds(CteTable):
    id: Column[int] = output_column(int)


class MatrixNumbers(CteTable):
    n: Column[int] = output_column(int)


@dataclass(frozen=True)
class MatrixEmployeeManager:
    employee_id: int
    manager_id: int | None


@dataclass(frozen=True)
class MatrixChainedOuterJoin:
    left_id: int | None
    right_id: int | None
    third_id: int | None


@dataclass(frozen=True)
class MatrixDecimalProduct:
    product: decimal.Decimal


matrix_left = MatrixLeft("relq_matrix_left")
matrix_right = MatrixRight("relq_matrix_right")
matrix_third = MatrixThird("relq_matrix_third")
matrix_people = MatrixPeople("relq_matrix_people")
matrix_semantics = MatrixSemantics("relq_matrix_semantics")


async def assert_relational_matrix(database: MatrixDatabase) -> None:
    """Assert the portable relational behavior expected from both engines."""
    right_join = (
        select(matrix_left.id.nullable(), matrix_right.id)
        .from_(matrix_left)
        .right_join(matrix_right, on=matrix_left.id.eq(matrix_right.id))
        .order_by(matrix_left.id.asc().nulls_last())
    )
    assert await database.fetch_all(right_join) == [(2, 2), (None, 3)]

    full_join = (
        select(matrix_left.id.nullable(), matrix_right.id.nullable())
        .from_(matrix_left)
        .full_join(matrix_right, on=matrix_left.id.eq(matrix_right.id))
        .order_by(matrix_left.id.asc().nulls_last())
    )
    assert await database.fetch_all(full_join) == [(1, None), (2, 2), (None, 3)]

    manager = matrix_people.as_("matrix_manager")
    employee_managers = (
        select_model(
            MatrixEmployeeManager,
            matrix_people.id,
            manager.id.nullable(),
        )
        .from_(matrix_people)
        .left_join(manager, on=matrix_people.manager_id.eq(manager.id))
        .order_by(matrix_people.id.asc())
    )
    assert await database.fetch_all(employee_managers) == [
        MatrixEmployeeManager(1, None),
        MatrixEmployeeManager(2, 1),
        MatrixEmployeeManager(3, 1),
        MatrixEmployeeManager(4, None),
        MatrixEmployeeManager(5, 2),
    ]

    chained_outer_join = (
        select_model(
            MatrixChainedOuterJoin,
            matrix_left.id.nullable(),
            matrix_right.id.nullable(),
            matrix_third.id.nullable(),
        )
        .from_(matrix_left)
        .full_join(matrix_right, on=matrix_left.id.eq(matrix_right.id))
        .full_join(matrix_third, on=matrix_right.id.eq(matrix_third.id))
        .order_by(matrix_left.id.asc().nulls_last(), matrix_right.id.asc().nulls_last())
    )
    assert await database.fetch_all(chained_outer_join) == [
        MatrixChainedOuterJoin(1, None, None),
        MatrixChainedOuterJoin(2, 2, None),
        MatrixChainedOuterJoin(None, 3, 3),
        MatrixChainedOuterJoin(None, None, 4),
    ]

    union = (
        select(matrix_left.id.as_("id"))
        .from_(matrix_left)
        .union(select(matrix_right.id.as_("id")).from_(matrix_right))
        .as_(MatrixIds, "union_ids")
    )
    union_all = (
        select(matrix_left.id.as_("id"))
        .from_(matrix_left)
        .union_all(select(matrix_right.id.as_("id")).from_(matrix_right))
        .as_(MatrixIds, "union_all_ids")
    )
    intersection = (
        select(matrix_left.id.as_("id"))
        .from_(matrix_left)
        .intersect(select(matrix_right.id.as_("id")).from_(matrix_right))
        .as_(MatrixIds, "intersection_ids")
    )
    difference = (
        select(matrix_left.id.as_("id"))
        .from_(matrix_left)
        .except_(select(matrix_right.id.as_("id")).from_(matrix_right))
        .as_(MatrixIds, "difference_ids")
    )
    assert await database.fetch_all(select(union.id).from_(union).order_by(union.id.asc())) == [
        (1,),
        (2,),
        (3,),
    ]
    assert await database.fetch_all(
        select(union_all.id).from_(union_all).order_by(union_all.id.asc())
    ) == [(1,), (2,), (2,), (3,)]
    assert await database.fetch_all(select(intersection.id).from_(intersection)) == [(2,)]
    assert await database.fetch_all(select(difference.id).from_(difference)) == [(1,)]

    active_ids = cte(MatrixActiveIds, "matrix_active_ids")
    cte_compound = (
        select(matrix_left.id.as_("id"))
        .from_(matrix_left)
        .where(matrix_left.id.eq(1))
        .union_all(select(active_ids.id.as_("id")).from_(active_ids))
        .with_(
            active_ids,
            select(matrix_left.id).from_(matrix_left).where(matrix_left.id.eq(2)),
        )
        .as_(MatrixIds, "cte_compound_ids")
    )
    assert await database.fetch_all(
        select(cte_compound.id).from_(cte_compound).order_by(cte_compound.id.asc())
    ) == [(1,), (2,)]

    manager_id = select(manager.id).from_(manager).where(manager.id.eq(matrix_people.manager_id))
    active_manager = manager_id.where(manager.active.is_true())
    scalar_rows = (
        select(matrix_people.id, scalar(manager_id))
        .from_(matrix_people)
        .order_by(matrix_people.id.asc())
    )
    assert await database.fetch_all(scalar_rows) == [(1, None), (2, 1), (3, 1), (4, None), (5, 2)]
    assert await database.fetch_all(
        select(matrix_people.id)
        .from_(matrix_people)
        .where(exists(active_manager))
        .order_by(matrix_people.id.asc())
    ) == [(2,), (3,), (5,)]

    grand_manager = matrix_people.as_("matrix_grand_manager")
    manager_has_manager = (
        select(manager.id)
        .from_(manager)
        .where(manager.id.eq(matrix_people.manager_id))
        .where(
            exists(
                select(grand_manager.id)
                .from_(grand_manager)
                .where(grand_manager.id.eq(manager.manager_id))
            )
        )
    )
    assert await database.fetch_all(
        select(matrix_people.id)
        .from_(matrix_people)
        .where(exists(manager_has_manager))
        .order_by(matrix_people.id.asc())
    ) == [(5,)]

    numbers = cte(MatrixNumbers, "matrix_numbers")
    recursive_numbers = (
        select(numbers.n)
        .from_(numbers)
        .with_recursive(
            numbers,
            select(matrix_left.id.as_("n"))
            .from_(matrix_left)
            .where(matrix_left.id.eq(1))
            .union_all(select(add(numbers.n, 1).as_("n")).from_(numbers).where(numbers.n.lt(3))),
        )
        .order_by(numbers.n.asc())
    )
    assert await database.fetch_all(recursive_numbers) == [(1,), (2,), (3,)]

    grouped_people = (
        select(matrix_people.active, count())
        .from_(matrix_people)
        .group_by(matrix_people.active)
        .having(count().gt(1))
        .order_by(matrix_people.active.asc())
    )
    assert await database.fetch_all(grouped_people) == [(True, 4)]

    active_reports = (
        select(matrix_people.active, count().as_("reports"))
        .from_(matrix_people)
        .group_by(matrix_people.active)
        .as_(MatrixActiveReports, "matrix_active_reports")
    )
    assert await database.fetch_all(
        select(active_reports.active, active_reports.reports)
        .from_(active_reports)
        .order_by(active_reports.active.asc())
    ) == [(False, 1), (True, 4)]

    grouped_peer = matrix_people.as_("matrix_grouped_peer")
    matching_active_group = (
        select(grouped_peer.active)
        .from_(grouped_peer)
        .group_by(grouped_peer.active)
        .having(grouped_peer.active.eq(matrix_people.active) & count().gt(1))
    )
    assert await database.fetch_all(
        select(matrix_people.id)
        .from_(matrix_people)
        .where(exists(matching_active_group))
        .order_by(matrix_people.id.asc())
    ) == [(1,), (2,), (4,), (5,)]

    nullable_comparison = matrix_semantics.nullable_value.eq(2)
    assert await database.fetch_all(
        select(matrix_semantics.id, nullable_comparison)
        .from_(matrix_semantics)
        .order_by(matrix_semantics.id.asc())
    ) == [(1, None), (2, True)]
    assert await database.fetch_all(
        select(matrix_semantics.id)
        .from_(matrix_semantics)
        .where(nullable_comparison)
        .order_by(matrix_semantics.id.asc())
    ) == [(2,)]
    assert await database.fetch_all(
        select(matrix_semantics.id)
        .from_(matrix_semantics)
        .where(nullable_comparison.is_true())
        .order_by(matrix_semantics.id.asc())
    ) == [(2,)]

    conditional_values = (
        select(
            matrix_semantics.id,
            case_when(matrix_semantics.id.eq(1), "first")
            .when(matrix_semantics.nullable_value.is_null(), "missing")
            .else_("other"),
            case_when(matrix_semantics.id.eq(1), "first").else_null(),
            coalesce(
                nullif(matrix_semantics.numerator, 3),
                nullif(matrix_semantics.denominator, 0),
            ),
            nullif(matrix_semantics.numerator, 3),
        )
        .from_(matrix_semantics)
        .order_by(matrix_semantics.id.asc())
    )
    assert await database.fetch_all(conditional_values) == [
        (1, "first", "first", 2, None),
        (2, "other", None, 5, 5),
    ]

    numeric_rows = await database.fetch_all(
        select(
            divide(matrix_semantics.numerator, matrix_semantics.denominator),
            divide(matrix_semantics.ratio, 2.0),
        )
        .from_(matrix_semantics)
        .order_by(matrix_semantics.id.asc())
    )
    assert numeric_rows == [(1, 0.75), (2, 1.25)]
    assert await database.fetch_all(
        select(sum(matrix_semantics.numerator), avg(matrix_semantics.numerator)).from_(
            matrix_semantics
        )
    ) == [(8, 4.0)]
    assert await database.fetch_all(
        select_model(
            row_adapter(MatrixDecimalProduct, decoders=(decimal_decoder(),)),
            multiply(matrix_semantics.price, matrix_semantics.price),
        )
        .from_(matrix_semantics)
        .order_by(matrix_semantics.id.asc())
    ) == [
        MatrixDecimalProduct(decimal.Decimal("2.25")),
        MatrixDecimalProduct(decimal.Decimal("6.25")),
    ]
