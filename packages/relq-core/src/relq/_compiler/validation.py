"""Defensive structural validation for private query AST values."""

from dataclasses import replace

from relq._analysis.ctes import cte_references
from relq._analysis.nullability import null_extended_sources
from relq._analysis.scopes import nested_queries, nested_scopes
from relq._analysis.sources import referenced_sources
from relq._analysis.walk import walk
from relq._ast import (
    AggregateNode,
    AliasNode,
    BetweenNode,
    BinaryNode,
    CaseNode,
    ColumnNode,
    CteNode,
    CteSourceNode,
    DefaultValuesSourceNode,
    DeleteNode,
    ExcludedNode,
    ExistsNode,
    FunctionNode,
    InNode,
    InsertNode,
    InsertRowsSourceNode,
    InsertSelectSourceNode,
    InsertValuesSourceNode,
    JsonTextNode,
    Node,
    NullableResultNode,
    QueryNode,
    RegexMatchNode,
    ScalarSubqueryNode,
    SelectNode,
    StarNode,
    TableSourceNode,
    UnaryNode,
    UpdateNode,
    UuidCastNode,
    ValueNode,
    WindowNode,
)
from relq._compiler.grouping import validate_analytic_clauses, validate_grouping


def validate_query(node: QueryNode) -> None:
    """Validate the complete query tree before dialect-specific rendering."""
    _validate_data_modifying_cte_placement(node)
    _validate_nested_cte_visibility(node)
    _validate_statement(node)


def _validate_statement(node: QueryNode, outer_sources: frozenset[str] = frozenset()) -> None:
    """Validate one statement against the relations visible where it appears.

    ``outer_sources`` is empty for a top-level statement and carries the CTEs
    already bound by the ``WITH`` chain when this statement *is* a later CTE.
    """
    match node:
        case SelectNode():
            _validate_select(node, outer_sources)
        case InsertNode():
            _validate_insert(node, outer_sources)
        case UpdateNode():
            if not node.values:
                raise ValueError("UPDATE requires at least one value")
            if not node.bounded:
                raise ValueError("UPDATE requires where() or explicit all_rows()")
            validate_dml_sources(node.table, node.where, node.values, node.returning)
        case DeleteNode():
            if not node.bounded:
                raise ValueError("DELETE requires where() or explicit all_rows()")
            validate_dml_sources(node.table, node.where, (), node.returning)


def _validate_select(node: SelectNode, outer_sources: frozenset[str] = frozenset()) -> None:
    validate_compound_shape(node)
    visible_ctes: set[str] = set()
    for cte in node.ctes:
        if cte.name in visible_ctes:
            raise ValueError(f"CTE {cte.name!r} is declared more than once")
        cte_sources = frozenset(visible_ctes) | outer_sources
        if isinstance(cte.query, SelectNode):
            if cte.recursive:
                cte_sources |= {cte.name}
                validate_recursive_cte(cte.query, cte.name)
            _validate_select(cte.query, cte_sources)
        else:
            _validate_data_modifying_cte(cte, cte.query, cte_sources)
        visible_ctes.add(cte.name)
    cte_names = frozenset(visible_ctes)
    validate_sources(node, outer_sources | cte_names)
    validate_analytic_clauses(node)
    validate_grouping(node)
    for compound in node.compounds:
        _validate_select(compound.query, outer_sources | cte_names)


def _validate_data_modifying_cte(
    cte: CteNode,
    query: InsertNode | UpdateNode | DeleteNode,
    outer_sources: frozenset[str],
) -> None:
    """Require a bounded, row-returning statement behind a data-modifying CTE.

    ``outer_sources`` is the scope already bound by the ``WITH`` chain, so an
    insert-from-select body can read a CTE declared before this one, exactly as
    a SELECT body can.
    """
    if cte.recursive:
        raise ValueError(f"recursive CTE {cte.name!r} requires a SELECT query")
    if not query.returning:
        raise ValueError(
            f"data-modifying CTE {cte.name!r} requires returning() to declare its output relation"
        )
    _validate_statement(query, outer_sources)


def _validate_nested_cte_visibility(node: QueryNode) -> None:
    """Reject a CTE reference that its enclosing ``WITH`` clauses have not bound.

    ``validate_sources`` applies this rule to the relations a query names in
    its own FROM/JOIN, but a derived table or a scalar/EXISTS/IN subquery can
    name a CTE too, and SQL binds ``WITH`` entries strictly in declaration
    order at every depth.  Without this pass a CTE body could reach a later
    entry through a subquery and only fail once the database parsed it.
    """
    for nested, visible in nested_scopes(node):
        if not isinstance(nested, SelectNode):
            continue
        # A nested query reads its own WITH clause too, exactly as the
        # outermost one does; ``visible`` only carries what encloses it.
        bound = visible | {cte.name for cte in nested.ctes}
        for source in (nested.from_source, *(join.source for join in nested.joins)):
            if isinstance(source, CteSourceNode) and source.name not in bound:
                raise ValueError(f"query references CTE {source.name!r} before it is declared")


def _validate_data_modifying_cte_placement(node: QueryNode) -> None:
    """Keep data-modifying CTEs in the outermost WITH clause.

    SQL runs a data-modifying CTE once for the whole statement rather than
    once per reference, so nesting one inside a derived table, subquery, or
    another CTE has no well-defined meaning and no dialect accepts it.
    """
    nested_names = sorted(
        cte.name
        for nested in nested_queries(node)
        if isinstance(nested, SelectNode)
        for cte in nested.ctes
        if not isinstance(cte.query, SelectNode)
    )
    if nested_names:
        names = ", ".join(nested_names)
        raise ValueError(f"data-modifying CTE(s) must be declared on the outermost query: {names}")


def _validate_insert(node: InsertNode, outer_sources: frozenset[str] = frozenset()) -> None:
    match node.source:
        case None:
            raise ValueError("INSERT requires values(), from_select(), or default_values()")
        case InsertSelectSourceNode(query, columns):
            if not columns:
                raise ValueError("insert-from-select requires target columns")
            if len(query.selections) != len(columns):
                raise ValueError("insert-from-select projection width changed after construction")
            _validate_select(query, outer_sources)
        case InsertRowsSourceNode(rows):
            columns = tuple(column for column, _ in rows[0])
            if any(tuple(column for column, _ in row) != columns for row in rows[1:]):
                raise ValueError(
                    "every INSERT VALUES row must contain the same columns in the same order"
                )
        case InsertValuesSourceNode() | DefaultValuesSourceNode():
            pass
    if isinstance(node.source, DefaultValuesSourceNode) and node.conflict is not None:
        raise ValueError("INSERT DEFAULT VALUES cannot use ON CONFLICT")
    validate_insert(node)


def validate_sources(node: SelectNode, outer_sources: frozenset[str] = frozenset()) -> None:
    if node.from_source is None:
        raise ValueError("SELECT queries must have from_() before compilation")
    for source in (node.from_source, *(join.source for join in node.joins)):
        if isinstance(source, CteSourceNode) and source.name not in outer_sources:
            raise ValueError(f"query references CTE {source.name!r} before it is declared")
    available = set(outer_sources)
    local: set[str] = set()
    _add_source(node.from_source.reference, available, local)
    for join in node.joins:
        _add_source(join.source.reference, available, local)
        missing = referenced_sources(join.predicate) - available
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(
                f"join predicate references source(s) not visible at this join: {names}"
            )
    references: set[str] = set()
    for expression in node.selections:
        references.update(referenced_sources(expression))
    references.update(referenced_sources(node.where))
    for expression in node.group_by:
        references.update(referenced_sources(expression))
    references.update(referenced_sources(node.having))
    for order in node.order_by:
        references.update(referenced_sources(order.expression))
    missing = references - available
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"query references source(s) not present in from_() or join(): {names}")
    validate_projected_nullability(node)


def validate_projected_nullability(node: SelectNode) -> None:
    """Require an explicit result-type transition after an outer join.

    A stored column's declaration describes rows in its own table, not rows
    manufactured by an outer join.  Expressions whose SQL value becomes NULL
    because they use a NULL-extended source must therefore carry the private
    marker produced by ``Expr.nullable()``.  Aggregates, windows, scalar
    subqueries, and predicates keep their own documented SQL result semantics.
    """
    nullable_sources = null_extended_sources(node)
    if not nullable_sources:
        return
    for selection in node.selections:
        _validate_selection_nullability(selection, nullable_sources)


def _validate_selection_nullability(node: Node, nullable_sources: frozenset[str]) -> None:
    if isinstance(node, NullableResultNode):
        return
    sources = _nullable_result_sources(node) & nullable_sources
    if not sources:
        return
    names = ", ".join(sorted(sources))
    raise ValueError(
        "outer join can NULL-extend selected expression from source(s) "
        f"{names}; call .nullable() to acknowledge its optional result type"
    )


def _nullable_result_sources(node: Node) -> set[str]:
    """Find sources that can make a selected scalar expression NULL.

    This is intentionally narrower than ``referenced_sources``.  Predicates
    and analytic expressions have their own result semantics, while ordinary
    column and arithmetic projections inherit a NULL-extended input.
    """
    match node:
        case ColumnNode(source, _):
            return {source}
        case AliasNode(expression, _) | NullableResultNode(expression) | UuidCastNode(expression):
            # A cast keeps its operand's declared nullability, so an outer join
            # still widens the result and still needs acknowledging.
            return _nullable_result_sources(expression)
        case BinaryNode(left, operator, right) if operator in {"+", "-", "*", "/"}:
            return _nullable_result_sources(left) | _nullable_result_sources(right)
        case CaseNode(branches, otherwise):
            result = _nullable_result_sources(otherwise)
            for _, value in branches:
                result.update(_nullable_result_sources(value))
            return result
        case (
            ValueNode()
            | UnaryNode()
            | FunctionNode()
            | AggregateNode()
            | WindowNode()
            | InNode()
            | BetweenNode()
            | ScalarSubqueryNode()
            | ExistsNode()
            | ExcludedNode()
            | StarNode()
            | BinaryNode()
            | JsonTextNode()
            | RegexMatchNode()
        ):
            # json_text already returns an optional result: an absent member and
            # a JSON null are indistinguishable, so an outer join cannot widen
            # what the declared type already admits.
            return set()
    raise TypeError(f"unsupported AST node: {node!r}")


def validate_compound_shape(node: SelectNode) -> None:
    """Defend the compiler boundary against ambiguous set-operation ASTs.

    Public builders require a declared derived relation before an ordered or
    paginated compound is used.  Repeating the rule here keeps handcrafted
    ``_Query`` implementations from bypassing that portable grammar.
    """
    if not node.compounds:
        return
    if node.order_by or node.limit is not None or node.offset is not None:
        raise ValueError(
            "compound queries cannot have ORDER BY, LIMIT, or OFFSET; bind the compound "
            "with as_() to a declared DerivedTable and modify the outer query"
        )
    for arm in node.compounds:
        if len(node.selections) != len(arm.query.selections):
            raise ValueError(
                "compound queries require equal projection widths: "
                f"left has {len(node.selections)}, right has {len(arm.query.selections)}"
            )
        if arm.query.ctes:
            raise ValueError("a compound arm cannot declare CTEs")
        if arm.query.compounds:
            raise ValueError("a compound arm cannot itself be compound")
        if arm.query.order_by or arm.query.limit is not None or arm.query.offset is not None:
            raise ValueError("compound arms cannot have ORDER BY, LIMIT, or OFFSET")


def validate_recursive_cte(node: SelectNode, name: str) -> None:
    """Require a non-recursive seed followed by recursive UNION ALL arms."""
    seed = replace(node, compounds=())
    if name in cte_references(seed):
        raise ValueError("a recursive CTE's seed query cannot reference itself")
    if not node.compounds:
        raise ValueError(
            "recursive CTE requires a non-recursive seed and a UNION ALL recursive arm"
        )
    for index, compound in enumerate(node.compounds, start=1):
        if compound.operator != "union all":
            raise ValueError("recursive CTE arms must be combined with union_all()")
        if name not in cte_references(compound.query):
            raise ValueError(f"recursive CTE arm {index} must reference its own CTE source")


def _add_source(reference: str, available: set[str], local: set[str]) -> None:
    if reference in local:
        raise ValueError("each FROM/JOIN source needs a distinct table name or alias")
    local.add(reference)
    available.add(reference)


def validate_dml_sources(
    table: TableSourceNode,
    where: Node | None,
    values: tuple[tuple[str, Node], ...],
    returning: tuple[Node, ...],
) -> None:
    references = referenced_sources(where)
    for _, value in values:
        references.update(referenced_sources(value))
    for value in returning:
        references.update(referenced_sources(value))
    missing = references - {table.reference}
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"DML query references source(s) outside its target table: {names}")


def validate_insert(node: InsertNode) -> None:
    values: tuple[tuple[str, Node], ...] = ()
    if isinstance(node.source, InsertValuesSourceNode):
        values = node.source.values
    elif isinstance(node.source, InsertRowsSourceNode):
        values = tuple(value for row in node.source.rows for value in row)
    if node.conflict is not None:
        values = (*values, *node.conflict.update_values)
    validate_dml_sources(node.table, None, values, node.returning)
    excluded_sources: set[str] = set()
    for _, value in values:
        excluded_sources.update(_excluded_sources(value))
    if excluded_sources:
        if node.conflict is None or node.conflict.action != "update":
            raise ValueError("excluded() can only be used in ON CONFLICT DO UPDATE assignments")
        if excluded_sources != {node.table.reference}:
            names = ", ".join(sorted(excluded_sources - {node.table.reference}))
            raise ValueError(f"excluded() columns must belong to the INSERT table: {names}")


def _excluded_sources(node: Node) -> set[str]:
    return {item.source for item in walk(node) if isinstance(item, ExcludedNode)}
