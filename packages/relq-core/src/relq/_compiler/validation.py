"""Defensive structural validation for private query AST values."""

from dataclasses import replace

from relq._analysis.ctes import cte_references
from relq._analysis.nullability import null_extended_sources
from relq._analysis.sources import referenced_sources
from relq._analysis.walk import walk
from relq._ast import (
    AggregateNode,
    AliasNode,
    BetweenNode,
    BinaryNode,
    CaseNode,
    ColumnNode,
    CteSourceNode,
    DefaultValuesSourceNode,
    DeleteNode,
    DerivedSourceNode,
    ExcludedNode,
    ExistsNode,
    FunctionNode,
    InNode,
    InsertNode,
    InsertRowsSourceNode,
    InsertSelectSourceNode,
    InsertValuesSourceNode,
    Node,
    NullableResultNode,
    QueryNode,
    ScalarSubqueryNode,
    SelectNode,
    StarNode,
    TableSourceNode,
    TemporalBinaryNode,
    TemporalCurrentNode,
    TemporalFunctionNode,
    UnaryNode,
    UpdateNode,
    ValueNode,
    WindowNode,
)
from relq._compiler._model import Dialect
from relq._compiler.grouping import validate_analytic_clauses, validate_grouping


def validate_query(node: QueryNode, dialect: Dialect) -> None:
    """Validate the complete query tree before dialect-specific rendering."""
    match node:
        case SelectNode():
            _validate_select(node, dialect)
        case InsertNode():
            _validate_insert(node, dialect)
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


def _validate_select(
    node: SelectNode, dialect: Dialect, outer_sources: frozenset[str] = frozenset()
) -> None:
    validate_compound_shape(node)
    visible_ctes: set[str] = set()
    for cte in node.ctes:
        if cte.name in visible_ctes:
            raise ValueError(f"CTE {cte.name!r} is declared more than once")
        cte_sources = frozenset(visible_ctes) | outer_sources
        if cte.recursive:
            cte_sources |= {cte.name}
            validate_recursive_cte(cte.query, cte.name)
        _validate_select(cte.query, dialect, cte_sources)
        visible_ctes.add(cte.name)
    cte_names = frozenset(visible_ctes)
    validate_sources(node, outer_sources | cte_names)
    validate_locks(node)
    _validate_dialect_nodes(node, dialect)
    validate_analytic_clauses(node)
    validate_grouping(node)
    for compound in node.compounds:
        _validate_select(compound.query, dialect, outer_sources | cte_names)
    if node.from_source is None:  # pragma: no cover - established by validate_sources()
        raise AssertionError("validated SELECT has no source")
    local_sources = frozenset(
        {node.from_source.reference, *(join.source.reference for join in node.joins)}
    )
    for source in (node.from_source, *(join.source for join in node.joins)):
        if isinstance(source, DerivedSourceNode):
            _validate_select(source.query, dialect, outer_sources | cte_names)
    for expression in (
        *node.selections,
        node.where,
        *node.group_by,
        node.having,
        *(order.expression for order in node.order_by),
    ):
        if expression is not None:
            _validate_nested_selects(expression, dialect, outer_sources | cte_names | local_sources)


def _validate_insert(node: InsertNode, dialect: Dialect) -> None:
    match node.source:
        case None:
            raise ValueError("INSERT requires values(), from_select(), or default_values()")
        case InsertSelectSourceNode(query, columns):
            if not columns:
                raise ValueError("insert-from-select requires target columns")
            if len(query.selections) != len(columns):
                raise ValueError("insert-from-select projection width changed after construction")
            _validate_select(query, dialect)
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


def validate_locks(node: SelectNode) -> None:
    """Defend PostgreSQL's row-identity restrictions at the AST boundary."""
    if not node.locks:
        return
    if node.distinct:
        raise ValueError("row locking cannot be combined with DISTINCT")
    if node.group_by or node.having is not None:
        raise ValueError("row locking cannot be combined with GROUP BY or HAVING")
    if node.compounds:
        raise ValueError("row locking cannot be combined with UNION, INTERSECT, or EXCEPT")
    if any(
        isinstance(descendant, (AggregateNode, WindowNode))
        for expression in (
            *node.selections,
            node.where,
            *node.group_by,
            node.having,
            *(order.expression for order in node.order_by),
        )
        if expression is not None
        for descendant in walk(expression)
    ):
        raise ValueError("row locking cannot be combined with aggregate or window expressions")
    direct_tables = {
        source
        for source in (node.from_source, *(join.source for join in node.joins))
        if isinstance(source, TableSourceNode)
    }
    nullable = null_extended_sources(node)
    for lock in node.locks:
        targets = direct_tables if not lock.of else set(lock.of)
        if not targets <= direct_tables:
            raise ValueError("lock OF must reference direct tables in from_() or join()")
        locked_names = {target.reference for target in targets}
        if nullable & locked_names:
            raise ValueError("row locking cannot lock a nullable outer-join side")


def _validate_dialect_nodes(node: SelectNode, dialect: Dialect) -> None:
    if dialect.name == "postgres":
        return
    for expression in (
        *node.selections,
        node.where,
        *node.group_by,
        node.having,
        *(order.expression for order in node.order_by),
    ):
        if expression is not None and any(
            isinstance(item, (TemporalCurrentNode, TemporalBinaryNode)) for item in walk(expression)
        ):
            raise ValueError(f"{dialect.name} does not support PostgreSQL temporal expressions")
    if node.locks:
        raise ValueError(f"{dialect.name} does not support PostgreSQL row locking")


def _validate_nested_selects(
    expression: Node, dialect: Dialect, outer_sources: frozenset[str]
) -> None:
    for item in walk(expression):
        match item:
            case ScalarSubqueryNode(query) | ExistsNode(query):
                _validate_select(query, dialect, outer_sources)
            case InNode(values=SelectNode() as query):
                _validate_select(query, dialect, outer_sources)
            case _:
                pass


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
        case AliasNode(expression, _) | NullableResultNode(expression):
            return _nullable_result_sources(expression)
        case BinaryNode(left, operator, right) if operator in {"+", "-", "*", "/"}:
            return _nullable_result_sources(left) | _nullable_result_sources(right)
        case TemporalBinaryNode(left, _, right):
            return _nullable_result_sources(left) | _nullable_result_sources(right)
        case TemporalFunctionNode(_, arguments):
            result: set[str] = set()
            for argument in arguments:
                result.update(_nullable_result_sources(argument))
            return result
        case CaseNode(branches, otherwise):
            result = _nullable_result_sources(otherwise)
            for _, value in branches:
                result.update(_nullable_result_sources(value))
            return result
        case (
            ValueNode()
            | TemporalCurrentNode()
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
        ):
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
