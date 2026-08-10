"""Focused tests for semantic analysis independent of SQL execution."""

import pytest
from relq import cte, scalar, select
from relq._analysis.ctes import cte_references
from relq._analysis.nullability import null_extended_sources
from relq._analysis.sources import referenced_sources
from relq._compiler.validation import validate_projected_nullability, validate_sources
from relq._query import select_node

from tests.fixtures import ActiveEmployees, employee_archive, employees


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
