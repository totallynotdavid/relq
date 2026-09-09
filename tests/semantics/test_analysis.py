"""Focused tests for semantic analysis independent of SQL execution."""

import pytest
from relq import count, cte, scalar, select
from relq._analysis.ctes import cte_references
from relq._analysis.nullability import null_extended_sources
from relq._analysis.scopes import nested_queries, nested_scopes
from relq._analysis.sources import referenced_sources
from relq._ast import SelectNode
from relq._compiler.validation import validate_projected_nullability, validate_sources
from relq._query import select_node

from tests.fixtures import ActiveEmployees, ManagerTotals, employee_archive, employees


def test_source_analysis_stops_at_nested_select_scopes() -> None:
    nested = select(employee_archive.id).from_(employee_archive).where(employee_archive.id.eq(1))
    query = select(employees.id, scalar(nested)).from_(employees).where(employees.id.eq(2))
    node = select_node(query)

    assert referenced_sources(node.where) == {"employees"}
    assert referenced_sources(node.selections[1]) == set()
    validate_sources(node)


def test_cte_analysis_walks_nested_derived_and_scalar_queries() -> None:
    active = cte(ActiveEmployees, "active")
    nested = select(active.id).from_(active)
    query = (
        select(employees.id, scalar(nested))
        .from_(employees)
        .with_(active, select(employees.id).from_(employees))
    )

    assert cte_references(select_node(query)) == {"active"}


def test_outer_join_analysis_and_validation_share_one_boundary() -> None:
    manager = employees.as_("manager")
    query = (
        select(employees.id, manager.id)
        .from_(employees)
        .left_join(manager, on=employees.manager_id.eq(manager.id))
    )
    node = select_node(query)

    assert null_extended_sources(node) == {"manager"}
    with pytest.raises(ValueError, match="NULL-extend.*manager"):
        validate_projected_nullability(node)


def test_nested_query_analysis_reaches_every_scope_below_a_statement() -> None:
    active = cte(ActiveEmployees, "active")
    totals = (
        select(employee_archive.id.as_("manager_id"), count().as_("reports"))
        .from_(employee_archive)
        .group_by(employee_archive.id)
        .as_(ManagerTotals, "totals")
    )
    correlated = select(active.id).from_(active)
    query = (
        select(employees.id, scalar(correlated))
        .from_(employees)
        .inner_join(totals, on=employees.id.eq(totals.manager_id))
        .with_(active, select(employees.id).from_(employees))
    )

    scopes = list(nested_queries(select_node(query)))

    assert all(isinstance(scope, SelectNode) for scope in scopes)
    assert [
        scope.from_source.reference
        for scope in scopes
        if isinstance(scope, SelectNode) and scope.from_source is not None
    ] == ["employees", "employee_archive", "active"]


def test_nested_scope_analysis_binds_cte_names_in_declaration_order() -> None:
    early = cte(ActiveEmployees, "early")
    later = cte(ActiveEmployees, "later")
    query = (
        select(employees.id)
        .from_(early)
        .with_(early, select(employees.id).from_(employees))
        .with_(
            later,
            select(employees.id)
            .from_(employees)
            .where(employees.id.in_(select(early.id).from_(early))),
        )
    )

    scopes = [
        (scope.from_source.reference, visible)
        for scope, visible in nested_scopes(select_node(query))
        if isinstance(scope, SelectNode) and scope.from_source is not None
    ]

    # The first CTE body sees nothing; the second sees the first, and so does
    # the subquery nested inside it.
    assert scopes == [
        ("employees", frozenset()),
        ("employees", frozenset({"early"})),
        ("early", frozenset({"early"})),
    ]
