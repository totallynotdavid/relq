"""Query-shape contracts: relations, composition, CTE visibility, and compounds."""

from dataclasses import replace
from typing import cast

import pytest
from relq import (
    Column,
    CteTable,
    SelectQuery,
    add,
    count,
    cte,
    exists,
    output_column,
    scalar,
    select,
)
from relq._compiler import compile_sqlite
from relq._compiler._model import Dialect
from relq._compiler.validation import validate_query
from relq._node_value import node_of
from relq._query import select_node

from tests.fixtures import ActiveEmployees, ManagerTotals, employees


def test_table_aliases_self_joins_and_everyday_predicates() -> None:
    manager = employees.as_("manager")
    query = (
        select(employees.name, manager.name)
        .from_(employees)
        .inner_join(manager, on=employees.manager_id.eq(manager.id))
        .where(employees.salary.between(10, 100))
        .where(employees.id.in_([1, 2, 3]))
        .where(add(employees.salary, 5).gt(20))
    )
    compiled = compile_sqlite(query)
    assert compiled.sql == (
        'select "employees"."name", "manager"."name" from "employees" '
        'inner join "employees" as "manager" on ("employees"."manager_id" = "manager"."id") '
        'where ((("employees"."salary" between ? and ?) and ("employees"."id" in (?, ?, ?))) '
        'and (("employees"."salary" + ?) > ?))'
    )
    assert compiled.parameters == (10, 100, 1, 2, 3, 5, 20)


def test_composition_supports_correlated_subqueries_derived_sources_unions_and_ctes() -> None:
    manager = employees.as_("manager")
    managers = select(manager.id).from_(manager).where(manager.salary.gt(50))
    correlated = select(manager.id).from_(manager).where(manager.id.eq(employees.manager_id))
    outer = (
        select(employees.id, scalar(correlated))
        .from_(employees)
        .where(exists(managers.where(manager.id.eq(employees.manager_id))))
    )
    compiled = compile_sqlite(outer)
    assert 'exists (select "manager"."id" from "employees" as "manager"' in compiled.sql
    assert (
        '(select "manager"."id" from "employees" as "manager" where ("manager"."id" = "employees"."manager_id"))'
        in compiled.sql
    )

    totals = (
        select(employees.manager_id, count().as_("reports"))
        .from_(employees)
        .group_by(employees.manager_id)
        .as_(ManagerTotals, "totals")
    )
    assert (
        'from (select "employees"."manager_id", count(*) as "reports"'
        in compile_sqlite(select(totals.reports).from_(totals).where(totals.reports.gte(1))).sql
    )

    active = cte(ActiveEmployees, "active")
    assert compile_sqlite(
        select(active.id).from_(active).with_(active, select(employees.id).from_(employees))
    ).sql.startswith('with "active" as (select')
    assert (
        " union all "
        in compile_sqlite(
            select(employees.id).from_(employees).union_all(select(employees.id).from_(employees))
        ).sql
    )
    assert (
        " intersect "
        in compile_sqlite(
            select(employees.id).from_(employees).intersect(select(employees.id).from_(employees))
        ).sql
    )
    assert (
        " except "
        in compile_sqlite(
            select(employees.id).from_(employees).except_(select(employees.id).from_(employees))
        ).sql
    )


def test_typed_relations_bind_declared_output_schemas() -> None:
    totals = (
        select(employees.manager_id, count().as_("reports"))
        .from_(employees)
        .group_by(employees.manager_id)
        .as_(ManagerTotals, "totals")
    )
    assert (
        'select "totals"."reports" from (select'
        in compile_sqlite(select(totals.reports).from_(totals).where(totals.reports.gte(1))).sql
    )
    with pytest.raises(ValueError, match="output alias"):
        select(count()).from_(employees).as_(ManagerTotals, "bad")


def test_recursive_ctes_require_a_seed_and_recursive_union_all_arm() -> None:
    active = cte(ActiveEmployees, "active")
    with pytest.raises(ValueError, match="requires a non-recursive seed"):
        compile_sqlite(
            select(active.id)
            .from_(active)
            .with_recursive(active, select(employees.id).from_(employees))
        )
    with pytest.raises(ValueError, match="seed query cannot reference itself"):
        compile_sqlite(
            select(active.id).from_(active).with_recursive(active, select(active.id).from_(active))
        )


def test_cte_visibility_and_compound_diagnostics() -> None:
    class Earlier(CteTable):
        id: Column[int] = output_column(int)

    class Later(CteTable):
        id: Column[int] = output_column(int)

    earlier = cte(Earlier, "earlier")
    later = cte(Later, "later")
    with pytest.raises(ValueError, match="before it is declared"):
        compile_sqlite(
            select(earlier.id).from_(earlier).with_(earlier, select(later.id).from_(later))
        )

    class Numbers(CteTable):
        n: Column[int] = output_column(int)

    numbers = cte(Numbers, "numbers")
    recursive = (
        select(numbers.n)
        .from_(numbers)
        .with_recursive(
            numbers,
            select(employees.id.as_("n"))
            .from_(employees)
            .union_all(select(numbers.n).from_(numbers)),
        )
    )
    assert compile_sqlite(recursive).sql.startswith('with recursive "numbers" as')

    mismatched = cast(
        SelectQuery[tuple[int]], select(employees.id, employees.name).from_(employees)
    )
    with pytest.raises(ValueError, match="projection widths"):
        select(employees.id).from_(employees).union(mismatched)


def test_compounds_require_declared_outer_relations_for_modifiers() -> None:
    arm = select(employees.id).from_(employees)
    compound = arm.union_all(select(employees.id).from_(employees))
    with pytest.raises(ValueError, match="compound queries cannot have ORDER BY"):
        compile_sqlite(compound.order_by(employees.id.asc()))
    with pytest.raises(ValueError, match="compound queries cannot have ORDER BY"):
        compile_sqlite(
            arm.order_by(employees.id.asc()).union_all(select(employees.id).from_(employees))
        )
    with pytest.raises(ValueError, match="compound queries cannot have ORDER BY"):
        validate_query(
            replace(select_node(compound), order_by=(node_of(employees.id.asc()),)),
            Dialect("sqlite", "?"),
        )
