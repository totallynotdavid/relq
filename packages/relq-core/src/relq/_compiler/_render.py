"""Recursive SQL rendering from immutable query nodes."""

from typing import Final

from relq._ast import (
    AggregateNode,
    AliasNode,
    BetweenNode,
    BinaryNode,
    CaseNode,
    ColumnNode,
    ConflictNode,
    CteSourceNode,
    DefaultValuesSourceNode,
    DeleteNode,
    DerivedSourceNode,
    ExcludedNode,
    ExistsNode,
    FrameBoundaryNode,
    FunctionNode,
    InNode,
    InsertNode,
    InsertRowsSourceNode,
    InsertSelectSourceNode,
    InsertValuesSourceNode,
    Node,
    NullableResultNode,
    OrderNode,
    QueryNode,
    ScalarSubqueryNode,
    SelectNode,
    SourceNode,
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
    ValueNode,
    WindowFrameNode,
    WindowNode,
)
from relq._compiler._model import CompiledQuery, Dialect


def render_query(node: QueryNode, dialect: Dialect) -> CompiledQuery:
    parameters: list[object] = []
    match node:
        case SelectNode():
            sql = _compile_select(node, dialect, parameters)
        case InsertNode():
            sql = _compile_insert(node, dialect, parameters)
        case UpdateNode():
            sql = _compile_update(node, dialect, parameters)
        case DeleteNode():
            sql = _compile_delete(node, dialect, parameters)
    if dialect.max_parameters is not None and len(parameters) > dialect.max_parameters:
        raise ValueError(
            f"{dialect.name} supports at most {dialect.max_parameters} parameters per statement; "
            f"this query has {len(parameters)}"
        )
    return CompiledQuery(sql, tuple(parameters))


def _compile_select(
    node: SelectNode,
    dialect: Dialect,
    parameters: list[object],
    outer_sources: frozenset[str] = frozenset(),
) -> str:
    if node.from_source is None:  # pragma: no cover - established by validate_query()
        raise AssertionError("attempted to render an unvalidated SELECT")
    visible_ctes: set[str] = set()
    for cte in node.ctes:
        cte_sources = frozenset(visible_ctes) | outer_sources
        if cte.recursive:
            cte_sources |= {cte.name}
        visible_ctes.add(cte.name)
    cte_names = frozenset(visible_ctes)
    scope = (
        outer_sources
        | cte_names
        | frozenset({node.from_source.reference, *(join.source.reference for join in node.joins)})
    )
    prefix = ""
    if node.ctes:
        compiled_ctes: list[str] = []
        prior_ctes: frozenset[str] = frozenset()
        for cte in node.ctes:
            cte_sources = outer_sources | prior_ctes
            if cte.recursive:
                cte_sources |= {cte.name}
            compiled_ctes.append(
                f"{_identifier(cte.name)} as ({_compile_select(cte.query, dialect, parameters, cte_sources)})"
            )
            prior_ctes |= {cte.name}
        keyword = "with recursive" if any(cte.recursive for cte in node.ctes) else "with"
        prefix = keyword + " " + ", ".join(compiled_ctes) + " "
    sql = "select " + ("distinct " if node.distinct else "")
    sql += ", ".join(_compile_node(x, dialect, parameters, scope) for x in node.selections)
    sql += " from " + _compile_source(node.from_source, dialect, parameters, outer_sources)
    for join in node.joins:
        sql += (
            f" {join.kind} join {_compile_source(join.source, dialect, parameters, outer_sources)}"
        )
        if join.kind != "cross":
            sql += " on " + _compile_node(join.predicate, dialect, parameters, scope)
    if node.where is not None:
        sql += " where " + _compile_node(node.where, dialect, parameters, scope)
    if node.group_by:
        sql += " group by " + ", ".join(
            _compile_node(x, dialect, parameters, scope) for x in node.group_by
        )
    if node.having is not None:
        sql += " having " + _compile_node(node.having, dialect, parameters, scope)
    if node.order_by:
        sql += " order by " + ", ".join(
            _compile_order(order, dialect, parameters, scope) for order in node.order_by
        )
    if node.limit is not None:
        sql += " limit " + str(node.limit)
    if node.offset is not None:
        sql += " offset " + str(node.offset)
    for lock in node.locks:
        sql += " for " + lock.strength
        if lock.of:
            sql += " of " + ", ".join(_identifier(source.reference) for source in lock.of)
        if lock.wait is not None:
            sql += " " + lock.wait
    for compound in node.compounds:
        sql += f" {compound.operator} " + _compile_select(
            compound.query, dialect, parameters, outer_sources | cte_names
        )
    return prefix + sql


def _compile_insert(node: InsertNode, dialect: Dialect, parameters: list[object]) -> str:
    match node.source:
        case InsertValuesSourceNode(values):
            columns = tuple(column for column, _ in values)
            body = (
                "values ("
                + ", ".join(_compile_node(value, dialect, parameters) for _, value in values)
                + ")"
            )
        case InsertRowsSourceNode(rows):
            columns = tuple(column for column, _ in rows[0])
            body = "values " + ", ".join(
                "(" + ", ".join(_compile_node(value, dialect, parameters) for _, value in row) + ")"
                for row in rows
            )
        case InsertSelectSourceNode(query, columns):
            body = _compile_select(query, dialect, parameters)
        case DefaultValuesSourceNode():
            sql = f"insert into {_compile_source(node.table, dialect, parameters)} default values"
            return sql + _compile_returning(node.returning, dialect, parameters)
        case None:  # pragma: no cover - established by validate_query()
            raise AssertionError("attempted to render an INSERT without a source")
    sql = (
        f"insert into {_compile_source(node.table, dialect, parameters)} "
        f"({', '.join(_identifier(column) for column in columns)}) {body}"
    )
    if node.conflict is not None:
        sql += _compile_conflict(node.conflict, dialect, parameters)
    return sql + _compile_returning(node.returning, dialect, parameters)


def _compile_conflict(conflict: ConflictNode, dialect: Dialect, parameters: list[object]) -> str:
    target = (
        ""
        if not conflict.columns
        else " (" + ", ".join(_identifier(column) for column in conflict.columns) + ")"
    )
    if conflict.target_where is not None:
        if not conflict.columns:  # pragma: no cover - established by validate_query()
            raise AssertionError("a conflict arbiter predicate requires target columns")
        target += " where " + _compile_index_predicate(conflict.target_where)
    if conflict.action == "nothing":
        if conflict.update_where is not None:  # pragma: no cover - builder invariant
            raise AssertionError("ON CONFLICT DO NOTHING cannot carry an update predicate")
        return " on conflict" + target + " do nothing"
    if conflict.action == "update":
        if not conflict.update_values:
            raise ValueError("ON CONFLICT DO UPDATE requires at least one assignment")
        assignments = ", ".join(
            f"{_identifier(column)} = {_compile_node(value, dialect, parameters)}"
            for column, value in conflict.update_values
        )
        sql = " on conflict" + target + " do update set " + assignments
        if conflict.update_where is not None:
            sql += " where " + _compile_node(conflict.update_where, dialect, parameters)
        return sql
    raise ValueError(f"unsupported conflict action: {conflict.action!r}")


_INDEX_PREDICATE_BINARY: Final = frozenset({"=", "<>", "<", "<=", ">", ">=", "and", "or"})
_INDEX_PREDICATE_UNARY: Final = frozenset(
    {"is null", "is not null", "is true", "is false", "is not true", "is not false"}
)


def _compile_index_predicate(node: Node) -> str:
    """Render a conflict arbiter's predicate with constants, never parameters.

    PostgreSQL infers the arbiter index by proving the index's own stored
    predicate from this one, and that proof compares parsed expression trees.
    A cached generic plan leaves ``$n`` parameters unfolded, so a parameterized
    arbiter predicate matches while the plan is custom and then stops matching
    once PostgreSQL switches to a generic plan -- the statement starts raising
    "no unique or exclusion constraint matching the ON CONFLICT specification"
    partway through a process's life.  Constants keep inference stable.

    The accepted node set is closed and narrower than ``_compile_node``: it is
    roughly what PostgreSQL itself allows in an index predicate, so an
    expression that could never match one is rejected here instead of at the
    database.
    """
    match node:
        case ColumnNode(source, name):
            return f"{_identifier(source)}.{_identifier(name)}"
        case ValueNode(value):
            return _constant(value)
        case BinaryNode(left, operator, right) if operator in _INDEX_PREDICATE_BINARY:
            return (
                f"({_compile_index_predicate(left)} {operator} {_compile_index_predicate(right)})"
            )
        case UnaryNode(operator, operand) if operator in _INDEX_PREDICATE_UNARY:
            return f"({_compile_index_predicate(operand)} {operator})"
        case InNode(expression, values, negated) if not isinstance(values, SelectNode):
            operator = "not in" if negated else "in"
            items = ", ".join(_compile_index_predicate(value) for value in values)
            return f"({_compile_index_predicate(expression)} {operator} ({items}))"
        case BetweenNode(expression, lower, upper, negated):
            operator = "not between" if negated else "between"
            return (
                f"({_compile_index_predicate(expression)} {operator} "
                f"{_compile_index_predicate(lower)} and {_compile_index_predicate(upper)})"
            )
        case _:
            raise ValueError(
                "a conflict-target predicate must repeat a partial index predicate using only "
                "columns, text/integer/boolean/NULL constants, comparisons, IN, BETWEEN, "
                f"IS checks, AND, and OR: {node!r}"
            )


def _constant(value: object) -> str:
    """Render one inlined index-predicate constant from a closed set of types."""
    match value:
        case None:
            return "null"
        case bool():
            return "true" if value else "false"
        case int():
            return str(value)
        case str():
            return "E'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
        case _:
            raise ValueError(
                "a conflict-target predicate constant must be text, an integer, a boolean, "
                f"or None: {value!r}"
            )


def _compile_update(node: UpdateNode, dialect: Dialect, parameters: list[object]) -> str:
    assignments = ", ".join(
        f"{_identifier(column)} = {_compile_node(value, dialect, parameters)}"
        for column, value in node.values
    )
    sql = f"update {_compile_source(node.table, dialect, parameters)} set {assignments}"
    if node.where is not None:
        sql += " where " + _compile_node(node.where, dialect, parameters)
    return sql + _compile_returning(node.returning, dialect, parameters)


def _compile_delete(node: DeleteNode, dialect: Dialect, parameters: list[object]) -> str:
    sql = f"delete from {_compile_source(node.table, dialect, parameters)}"
    if node.where is not None:
        sql += " where " + _compile_node(node.where, dialect, parameters)
    return sql + _compile_returning(node.returning, dialect, parameters)


def _compile_returning(nodes: tuple[Node, ...], dialect: Dialect, parameters: list[object]) -> str:
    if not nodes:
        return ""
    if not dialect.supports_returning:
        raise ValueError(f"{dialect.name} does not support RETURNING")
    return " returning " + ", ".join(_compile_node(node, dialect, parameters) for node in nodes)


def _compile_source(
    source: SourceNode,
    dialect: Dialect,
    parameters: list[object],
    outer_sources: frozenset[str] = frozenset(),
) -> str:
    match source:
        case TableSourceNode(name, alias):
            sql = _identifier(name)
            return sql if alias is None else f"{sql} as {_identifier(alias)}"
        case CteSourceNode(name):
            return _identifier(name)
        case DerivedSourceNode(query, alias):
            return f"({_compile_select(query, dialect, parameters, outer_sources)}) as {_identifier(alias)}"
    raise TypeError(f"unsupported source: {source!r}")


def _compile_node(
    node: Node,
    dialect: Dialect,
    parameters: list[object],
    outer_sources: frozenset[str] = frozenset(),
) -> str:
    match node:
        case ColumnNode(source, name):
            return f"{_identifier(source)}.{_identifier(name)}"
        case ValueNode(value):
            parameters.append(value)
            return dialect.placeholder if dialect.placeholder == "?" else f"${len(parameters)}"
        case TemporalClockNode(kind):
            return {
                "current_date": "current_date",
                "current_time": "current_time",
                "local_time": "localtime",
                "local_timestamp": "localtimestamp",
            }.get(kind, f"{kind}()")
        case BinaryNode(left, operator, right):
            return f"({_compile_node(left, dialect, parameters, outer_sources)} {operator} {_compile_node(right, dialect, parameters, outer_sources)})"
        case TemporalMakeDateNode(year, month, day):
            return _compile_temporal_call(
                "make_date", (year, month, day), dialect, parameters, outer_sources
            )
        case TemporalMakeTimeNode(hour, minute, second):
            return _compile_temporal_call(
                "make_time", (hour, minute, second), dialect, parameters, outer_sources
            )
        case TemporalMakeTimestampNode(year, month, day, hour, minute, second):
            return _compile_temporal_call(
                "make_timestamp",
                (year, month, day, hour, minute, second),
                dialect,
                parameters,
                outer_sources,
            )
        case TemporalMakeTimestamptzNode(year, month, day, hour, minute, second, zone):
            arguments = (year, month, day, hour, minute, second)
            if zone is not None:
                arguments += (zone,)
            return _compile_temporal_call(
                "make_timestamptz", arguments, dialect, parameters, outer_sources
            )
        case TemporalMakeIntervalNode(components):
            return (
                "make_interval("
                + ", ".join(
                    f"{name} => {_compile_node(value, dialect, parameters, outer_sources)}"
                    for name, value in components
                )
                + ")"
            )
        case TemporalEpochNode(seconds):
            return _compile_temporal_call(
                "to_timestamp", (seconds,), dialect, parameters, outer_sources
            )
        case TemporalArithmeticNode(timestamp, operator, interval):
            interval_sql = _compile_node(interval, dialect, parameters, outer_sources)
            if isinstance(interval, ValueNode):
                interval_sql += "::interval"
            return (
                f"({_compile_node(timestamp, dialect, parameters, outer_sources)} "
                f"{operator} {interval_sql})"
            )
        case TemporalDifferenceNode(left, right):
            return (
                f"({_compile_node(left, dialect, parameters, outer_sources)} - "
                f"{_compile_node(right, dialect, parameters, outer_sources)})"
            )
        case TemporalIntervalUnaryNode(interval):
            return f"(-{_compile_node(interval, dialect, parameters, outer_sources)})"
        case TemporalIntervalScaleNode(interval, operator, factor):
            return (
                f"({_compile_node(interval, dialect, parameters, outer_sources)} {operator} "
                f"{_compile_node(factor, dialect, parameters, outer_sources)})"
            )
        case TemporalTimezoneNode(expression, zone):
            return (
                f"({_compile_node(expression, dialect, parameters, outer_sources)} at time zone "
                f"{_compile_node(zone, dialect, parameters, outer_sources)})"
            )
        case TemporalExtractNode(field, expression):
            return (
                f"extract({_sql_literal(field)} from "
                f"{_compile_node(expression, dialect, parameters, outer_sources)})"
            )
        case TemporalTruncNode(unit, expression):
            return (
                "date_trunc("
                + ", ".join(
                    (
                        _sql_literal(unit),
                        _compile_node(expression, dialect, parameters, outer_sources),
                    )
                )
                + ")"
            )
        case TemporalTruncTimestamptzNode(unit, expression, zone):
            return (
                "date_trunc("
                + ", ".join(
                    (
                        _sql_literal(unit),
                        _compile_node(expression, dialect, parameters, outer_sources),
                        _compile_node(zone, dialect, parameters, outer_sources),
                    )
                )
                + ")"
            )
        case TemporalBinNode(stride, expression, origin):
            stride_sql = _compile_node(stride, dialect, parameters, outer_sources)
            if isinstance(stride, ValueNode):
                stride_sql += "::interval"
            return (
                "date_bin("
                + ", ".join(
                    (
                        stride_sql,
                        _compile_node(expression, dialect, parameters, outer_sources),
                        _compile_node(origin, dialect, parameters, outer_sources),
                    )
                )
                + ")"
            )
        case TemporalAgeNode(left, right):
            return _compile_temporal_call("age", (left, right), dialect, parameters, outer_sources)
        case TemporalOverlapsNode(left_start, left_end, right_start, right_end):
            left = ", ".join(
                _compile_node(item, dialect, parameters, outer_sources)
                for item in (left_start, left_end)
            )
            right = ", ".join(
                _compile_node(item, dialect, parameters, outer_sources)
                for item in (right_start, right_end)
            )
            return f"(({left}) overlaps ({right}))"
        case TemporalJustifyNode(kind, interval):
            return _compile_temporal_call(kind, (interval,), dialect, parameters, outer_sources)
        case UnaryNode(operator, operand):
            return f"({_compile_node(operand, dialect, parameters, outer_sources)} {operator})"
        case FunctionNode(name, arguments):
            return (
                f"{name}("
                + ", ".join(_compile_node(x, dialect, parameters, outer_sources) for x in arguments)
                + ")"
            )
        case CaseNode(branches, otherwise):
            return (
                "case "
                + " ".join(
                    f"when {_compile_node(condition, dialect, parameters, outer_sources)} "
                    f"then {_compile_node(value, dialect, parameters, outer_sources)}"
                    for condition, value in branches
                )
                + f" else {_compile_node(otherwise, dialect, parameters, outer_sources)} end"
            )
        case AggregateNode(name, arguments, filter):
            aggregate = (
                f"{name}("
                + ", ".join(_compile_node(x, dialect, parameters, outer_sources) for x in arguments)
                + ")"
            )
            if filter is None:
                return aggregate
            return (
                f"{aggregate} filter (where "
                f"{_compile_node(filter, dialect, parameters, outer_sources)})"
            )
        case WindowNode(expression, partition_by, order_by, frame, exclusion):
            clauses: list[str] = []
            if exclusion is not None and frame is None:
                raise ValueError("window exclusions require an explicit frame")
            if partition_by:
                clauses.append(
                    "partition by "
                    + ", ".join(
                        _compile_node(item, dialect, parameters, outer_sources)
                        for item in partition_by
                    )
                )
            if order_by:
                clauses.append(
                    "order by "
                    + ", ".join(
                        _compile_order(order, dialect, parameters, outer_sources)
                        for order in order_by
                    )
                )
            if frame is not None:
                clauses.append(_compile_window_frame(frame, order_by, exclusion))
            return f"{_compile_node(expression, dialect, parameters, outer_sources)} over ({' '.join(clauses)})"
        case AliasNode(expression, alias):
            return f"{_compile_node(expression, dialect, parameters, outer_sources)} as {_identifier(alias)}"
        case NullableResultNode(expression):
            return _compile_node(expression, dialect, parameters, outer_sources)
        case StarNode():
            return "*"
        case InNode() as membership:
            operator = "not in" if membership.negated else "in"
            left = _compile_node(membership.expression, dialect, parameters, outer_sources)
            if isinstance(membership.values, SelectNode):
                nested = _compile_select(membership.values, dialect, parameters, outer_sources)
                return f"({left} {operator} ({nested}))"
            compiled = ", ".join(
                _compile_node(value, dialect, parameters, outer_sources)
                for value in membership.values
            )
            return f"({left} {operator} ({compiled}))"
        case BetweenNode(expression, lower, upper, negated):
            operator = "not between" if negated else "between"
            return f"({_compile_node(expression, dialect, parameters, outer_sources)} {operator} {_compile_node(lower, dialect, parameters, outer_sources)} and {_compile_node(upper, dialect, parameters, outer_sources)})"
        case ScalarSubqueryNode(query):
            return f"({_compile_select(query, dialect, parameters, outer_sources)})"
        case ExistsNode(query, negated):
            operator = "not exists" if negated else "exists"
            return f"{operator} ({_compile_select(query, dialect, parameters, outer_sources)})"
        case ExcludedNode(_, name):
            return f"excluded.{_identifier(name)}"
    raise TypeError(f"unsupported AST node: {node!r}")


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    """Render a closed compiler-owned text token, never user SQL."""
    return "'" + value.replace("'", "''") + "'"


def _compile_temporal_call(
    name: str,
    arguments: tuple[Node, ...],
    dialect: Dialect,
    parameters: list[object],
    outer_sources: frozenset[str],
) -> str:
    return (
        f"{name}("
        + ", ".join(
            _compile_node(argument, dialect, parameters, outer_sources) for argument in arguments
        )
        + ")"
    )


def _compile_order(
    order: OrderNode,
    dialect: Dialect,
    parameters: list[object],
    outer_sources: frozenset[str],
) -> str:
    sql = f"{_compile_node(order.expression, dialect, parameters, outer_sources)} {order.direction}"
    if order.nulls is None:
        return sql
    if order.nulls not in {"first", "last"}:
        raise ValueError(f"unsupported NULL placement: {order.nulls!r}")
    return f"{sql} nulls {order.nulls}"


def _compile_window_frame(
    frame: WindowFrameNode,
    order_by: tuple[OrderNode, ...],
    exclusion: str | None,
) -> str:
    if frame.kind == "range" and _frame_has_offset(frame) and len(order_by) != 1:
        raise ValueError(
            "RANGE frames with an offset require exactly one window order_by expression"
        )
    sql = (
        f"{frame.kind} between {_compile_frame_boundary(frame.start)} "
        f"and {_compile_frame_boundary(frame.end)}"
    )
    if exclusion is None:
        return sql
    if exclusion not in {"no others", "current row", "group", "ties"}:
        raise ValueError(f"unsupported window exclusion: {exclusion!r}")
    return f"{sql} exclude {exclusion}"


def _frame_has_offset(frame: WindowFrameNode) -> bool:
    return frame.start.amount is not None or frame.end.amount is not None


def _compile_frame_boundary(boundary: FrameBoundaryNode) -> str:
    if boundary.amount is None:
        return boundary.kind
    return f"{boundary.amount} {boundary.kind}"
