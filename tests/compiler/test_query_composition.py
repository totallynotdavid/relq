"""Compiler contracts for portable query composition."""

import pytest
from relq import Column, CteTable, Table, column, cte, exists, output_column, select
from relq._compiler import compile_sqlite


class Items(Table):
    id: Column[int] = column(int)


class ItemIds(CteTable):
    id: Column[int] = output_column(int)


items = Items("items")


def test_compound_arm_cannot_introduce_its_own_cte_scope() -> None:
    ids = cte(ItemIds, "ids")
    arm = select(items.id.as_("id")).from_(items).with_(ids, select(items.id).from_(items))
    with pytest.raises(ValueError, match="compound arm cannot declare CTEs"):
        compile_sqlite(select(items.id.as_("id")).from_(items).union_all(arm))


def test_recursive_cte_seed_cannot_reference_itself_through_a_subquery() -> None:
    ids = cte(ItemIds, "ids")
    self_referencing_seed = (
        select(items.id.as_("id"))
        .from_(items)
        .where(exists(select(ids.id).from_(ids)))
        .union_all(select(ids.id).from_(ids))
    )
    with pytest.raises(ValueError, match="seed query cannot reference itself"):
        compile_sqlite(select(ids.id).from_(ids).with_recursive(ids, self_referencing_seed))


def test_every_recursive_cte_arm_must_reference_the_recursive_source() -> None:
    ids = cte(ItemIds, "ids")
    recursive_definition = (
        select(items.id.as_("id")).from_(items).union_all(select(items.id.as_("id")).from_(items))
    )
    with pytest.raises(ValueError, match="must reference its own CTE source"):
        compile_sqlite(select(ids.id).from_(ids).with_recursive(ids, recursive_definition))
