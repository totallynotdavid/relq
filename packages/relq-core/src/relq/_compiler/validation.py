"""Defensive structural validation for private query AST values."""

from collections.abc import Iterable
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
    DerivedSourceNode,
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
    TemporalAgeNode,
    TemporalArithmeticNode,
    TemporalBinNode,
    TemporalClockNode,
    TemporalDifferenceNode,
    TemporalEpochNode,
    TemporalExtractNode,
    TemporalIntervalScaleNode,
    TemporalIntervalUnaryNode,
    TemporalJustifyNode,
    TemporalMakeDateNode,
    TemporalMakeIntervalNode,
    TemporalMakeTimeNode,
    TemporalMakeTimestampNode,
    TemporalMakeTimestamptzNode,
    TemporalOverlapsNode,
    TemporalTimezoneNode,
    TemporalTruncNode,
    TemporalTruncTimestamptzNode,
    UnaryNode,
    UpdateNode,
    UuidCastNode,
    ValueNode,
    WindowNode,
)
from relq._compiler._model import Dialect
from relq._compiler.grouping import validate_analytic_clauses, validate_grouping
from relq._temporal import EXTRACT_FIELD_VALUES, TRUNC_UNIT_VALUES
from relq.rows import Interval


def validate_query(node: QueryNode, dialect: Dialect) -> None:
    """Validate the complete query tree before dialect-specific rendering."""
    _validate_data_modifying_cte_placement(node)
    _validate_nested_cte_visibility(node)
    _validate_statement(node, dialect)


def _validate_statement(
    node: QueryNode, dialect: Dialect, outer_sources: frozenset[str] = frozenset()
) -> None:
    """Validate one statement against the relations visible where it appears.

    ``outer_sources`` is empty for a top-level statement and carries the CTEs
    already bound by the ``WITH`` chain when this statement *is* a later CTE.
    """
    match node:
        case SelectNode():
            _validate_select(node, dialect, outer_sources)
        case InsertNode():
            _validate_insert(node, dialect, outer_sources)
        case UpdateNode():
            if not node.values:
                raise ValueError("UPDATE requires at least one value")
            if not node.bounded:
                raise ValueError("UPDATE requires where() or explicit all_rows()")
            validate_dml_sources(node.table, node.where, node.values, node.returning)
            _validate_dml_dialect(node, dialect, outer_sources)
        case DeleteNode():
            if not node.bounded:
                raise ValueError("DELETE requires where() or explicit all_rows()")
            validate_dml_sources(node.table, node.where, (), node.returning)
            _validate_dml_dialect(node, dialect, outer_sources)


def _validate_select(
    node: SelectNode, dialect: Dialect, outer_sources: frozenset[str] = frozenset()
) -> None:
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
            _validate_select(cte.query, dialect, cte_sources)
        else:
            _validate_data_modifying_cte(cte, cte.query, dialect, cte_sources)
        visible_ctes.add(cte.name)
    cte_names = frozenset(visible_ctes)
    validate_sources(node, outer_sources | cte_names)
    validate_locks(node)
    _validate_dialect_nodes(node, dialect)
    validate_analytic_clauses(node)
    validate_grouping(node)
    for compound in node.compounds:
        if compound.query.locks:
            raise ValueError("row locking cannot be combined with UNION, INTERSECT, or EXCEPT")
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
        *(join.predicate for join in node.joins),
        node.where,
        *node.group_by,
        node.having,
        *(order.expression for order in node.order_by),
    ):
        if expression is not None:
            _validate_nested_selects(expression, dialect, outer_sources | cte_names | local_sources)


def _validate_data_modifying_cte(
    cte: CteNode,
    query: InsertNode | UpdateNode | DeleteNode,
    dialect: Dialect,
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
    _validate_statement(query, dialect, outer_sources)


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


def _validate_insert(
    node: InsertNode, dialect: Dialect, outer_sources: frozenset[str] = frozenset()
) -> None:
    match node.source:
        case None:
            raise ValueError("INSERT requires values(), from_select(), or default_values()")
        case InsertSelectSourceNode(query, columns):
            if not columns:
                raise ValueError("insert-from-select requires target columns")
            if len(query.selections) != len(columns):
                raise ValueError("insert-from-select projection width changed after construction")
            _validate_select(query, dialect, outer_sources)
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
    _validate_insert_dialect(node, dialect, outer_sources)


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
    for expression in (
        *node.selections,
        node.where,
        *(join.predicate for join in node.joins),
        *node.group_by,
        node.having,
        *(order.expression for order in node.order_by),
    ):
        if expression is not None:
            _validate_expression_dialect(expression, dialect)
    if node.locks and not dialect.supports_row_locking:
        raise ValueError(f"{dialect.name} does not support PostgreSQL row locking")


def _validate_expression_dialect(expression: Node, dialect: Dialect) -> None:
    temporal_types = (
        TemporalClockNode,
        TemporalMakeDateNode,
        TemporalMakeTimeNode,
        TemporalMakeTimestampNode,
        TemporalMakeTimestamptzNode,
        TemporalMakeIntervalNode,
        TemporalEpochNode,
        TemporalArithmeticNode,
        TemporalDifferenceNode,
        TemporalIntervalUnaryNode,
        TemporalIntervalScaleNode,
        TemporalTimezoneNode,
        TemporalExtractNode,
        TemporalTruncNode,
        TemporalTruncTimestamptzNode,
        TemporalBinNode,
        TemporalAgeNode,
        TemporalOverlapsNode,
        TemporalJustifyNode,
    )
    for item in walk(expression):
        if not isinstance(item, temporal_types):
            continue
        if not dialect.supports_temporal_arithmetic:
            raise ValueError(f"{dialect.name} does not support PostgreSQL temporal expressions")
        _validate_temporal_node(item)


def _validate_temporal_node(node: Node) -> None:
    """Validate compiler-owned temporal tokens before dialect rendering."""
    match node:
        case TemporalClockNode(kind) if kind not in {
            "transaction_timestamp",
            "statement_timestamp",
            "clock_timestamp",
            "current_date",
            "current_time",
            "local_time",
            "local_timestamp",
        }:
            raise ValueError(f"unsupported temporal clock: {kind!r}")
        case TemporalMakeIntervalNode(components):
            allowed = {"years", "months", "weeks", "days", "hours", "mins", "secs"}
            names = tuple(name for name, _ in components)
            if any(name not in allowed for name in names):
                raise ValueError("unsupported make_interval component")
            if len(names) != len(set(names)):
                raise ValueError("make_interval components must be unique")
        case TemporalExtractNode(field, _) if field not in EXTRACT_FIELD_VALUES:
            raise ValueError(f"unsupported extract field: {field!r}")
        case TemporalTruncNode(unit, _) | TemporalTruncTimestamptzNode(unit, _, _) if (
            unit not in TRUNC_UNIT_VALUES
        ):
            raise ValueError(f"unsupported date_trunc unit: {unit!r}")
        case TemporalBinNode(ValueNode(Interval(months, days, microseconds)), _, _):
            if months or days * 86_400_000_000 + microseconds <= 0:
                raise ValueError(
                    "date_bin stride must be positive and cannot contain month-or-larger units"
                )
        case TemporalArithmeticNode(_, operator, _) if operator not in {"+", "-"}:
            raise ValueError(f"unsupported temporal arithmetic operator: {operator!r}")
        case TemporalIntervalScaleNode(_, operator, _) if operator not in {"*", "/"}:
            raise ValueError(f"unsupported interval scale operator: {operator!r}")
        case TemporalJustifyNode(kind, _) if kind not in {
            "justify_days",
            "justify_hours",
            "justify_interval",
        }:
            raise ValueError(f"unsupported interval justification: {kind!r}")
        case _:
            pass


def _validate_dml_dialect(
    node: UpdateNode | DeleteNode, dialect: Dialect, outer_sources: frozenset[str]
) -> None:
    expressions: list[Node] = []
    if node.where is not None:
        expressions.append(node.where)
    if isinstance(node, UpdateNode):
        expressions.extend(value for _, value in node.values)
    expressions.extend(node.returning)
    _reject_aggregates_and_windows(
        expressions, "UPDATE" if isinstance(node, UpdateNode) else "DELETE"
    )
    for expression in expressions:
        _validate_expression_dialect(expression, dialect)
        _validate_nested_selects(expression, dialect, outer_sources | {node.table.reference})


def _validate_insert_dialect(
    node: InsertNode, dialect: Dialect, outer_sources: frozenset[str]
) -> None:
    expressions: list[Node] = [*node.returning]
    if isinstance(node.source, InsertValuesSourceNode):
        expressions.extend(value for _, value in node.source.values)
    elif isinstance(node.source, InsertRowsSourceNode):
        expressions.extend(value for row in node.source.rows for _, value in row)
    if node.conflict is not None:
        expressions.extend(value for _, value in node.conflict.update_values)
    _reject_aggregates_and_windows(expressions, "INSERT")
    for expression in expressions:
        _validate_expression_dialect(expression, dialect)
        _validate_nested_selects(expression, dialect, outer_sources | {node.table.reference})


def _reject_aggregates_and_windows(expressions: Iterable[Node], statement: str) -> None:
    """Keep set-level expressions out of a row-level statement's own clauses.

    ``walk`` stops at a nested SELECT, so an aggregate inside a subquery, where
    it is legal, is not seen here.
    """
    if any(
        isinstance(descendant, (AggregateNode, WindowNode))
        for expression in expressions
        for descendant in walk(expression)
    ):
        raise ValueError(f"{statement} cannot contain aggregate or window expressions")


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
        case AliasNode(expression, _) | NullableResultNode(expression) | UuidCastNode(expression):
            # A cast keeps its operand's declared nullability, so an outer join
            # still widens the result and still needs acknowledging.
            return _nullable_result_sources(expression)
        case BinaryNode(left, operator, right) if operator in {"+", "-", "*", "/"}:
            return _nullable_result_sources(left) | _nullable_result_sources(right)
        case TemporalArithmeticNode(timestamp, _, interval):
            return _nullable_result_sources(timestamp) | _nullable_result_sources(interval)
        case TemporalDifferenceNode(left, right) | TemporalAgeNode(left, right):
            return _nullable_result_sources(left) | _nullable_result_sources(right)
        case TemporalMakeDateNode(year, month, day):
            return (
                _nullable_result_sources(year)
                | _nullable_result_sources(month)
                | _nullable_result_sources(day)
            )
        case TemporalMakeTimeNode(hour, minute, second):
            return (
                _nullable_result_sources(hour)
                | _nullable_result_sources(minute)
                | _nullable_result_sources(second)
            )
        case TemporalMakeTimestampNode(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
        ):
            return (
                _nullable_result_sources(year)
                | _nullable_result_sources(month)
                | _nullable_result_sources(day)
                | _nullable_result_sources(hour)
                | _nullable_result_sources(minute)
                | _nullable_result_sources(second)
            )
        case TemporalMakeTimestamptzNode(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
            zone=zone,
        ):
            return (
                _nullable_result_sources(year)
                | _nullable_result_sources(month)
                | _nullable_result_sources(day)
                | _nullable_result_sources(hour)
                | _nullable_result_sources(minute)
                | _nullable_result_sources(second)
                | (set() if zone is None else _nullable_result_sources(zone))
            )
        case TemporalMakeIntervalNode(components):
            result: set[str] = set()
            for _, argument in components:
                result.update(_nullable_result_sources(argument))
            return result
        case (
            TemporalEpochNode(seconds)
            | TemporalIntervalUnaryNode(seconds)
            | TemporalJustifyNode(_, seconds)
        ):
            return _nullable_result_sources(seconds)
        case TemporalIntervalScaleNode(interval, _, factor):
            return _nullable_result_sources(interval) | _nullable_result_sources(factor)
        case TemporalTimezoneNode(expression, zone):
            return _nullable_result_sources(expression) | _nullable_result_sources(zone)
        case TemporalExtractNode(_, expression):
            return _nullable_result_sources(expression)
        case TemporalTruncNode(_, expression):
            return _nullable_result_sources(expression)
        case TemporalTruncTimestamptzNode(_, expression, zone):
            return _nullable_result_sources(expression) | _nullable_result_sources(zone)
        case TemporalBinNode(stride, expression, origin):
            return (
                _nullable_result_sources(stride)
                | _nullable_result_sources(expression)
                | _nullable_result_sources(origin)
            )
        case CaseNode(branches, otherwise):
            result = _nullable_result_sources(otherwise)
            for _, value in branches:
                result.update(_nullable_result_sources(value))
            return result
        case (
            ValueNode()
            | TemporalClockNode()
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
            | TemporalOverlapsNode()
        ):
            # json_text already returns an optional result: an absent member and
            # a JSON null are indistinguishable, so an outer join cannot widen
            # what the declared type already admits.  overlaps() is likewise a
            # NullablePredicate whatever its operands are.
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
