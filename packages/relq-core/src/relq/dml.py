# ruff: noqa: UP046
"""Immutable data-modification builders.

Payloads intentionally use keyword arguments.  Python cannot derive a
``TypedDict`` from arbitrary column descriptors; generated schemas can expose
their own payload TypedDicts while this API validates target column names at
runtime and retains typed ``RETURNING`` rows.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Generic, Literal, Self, TypeVar, overload

from relq._ast import (
    ColumnNode,
    ConflictNode,
    DefaultValuesSourceNode,
    DeleteNode,
    InsertNode,
    InsertRowsSourceNode,
    InsertSelectSourceNode,
    InsertSourceNode,
    InsertValuesSourceNode,
    Node,
    UpdateNode,
    ValueNode,
)
from relq._node_value import expression_node, node_of
from relq._query import Query, extract_query, new_query, select_node
from relq.expressions import BooleanExpression, Column, Expr, Expression, Table
from relq.expressions.relations import table_node
from relq.query import SelectQuery
from relq.rows import RowAdapter, row_adapter


def _values(table: Table, entries: dict[str, object]) -> tuple[tuple[str, Node], ...]:
    if not entries:
        raise ValueError("values requires at least one column")
    known = table.column_names()
    unknown = entries.keys() - known
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"unknown column(s) for {table.table_name}: {names}")
    return tuple(
        (name, expression_node(item) if isinstance(item, Expression) else ValueNode(item))
        for name, item in entries.items()
    )


def _target_column_names(
    table: Table, columns: tuple[object, ...], *, allow_empty: bool = False
) -> tuple[str, ...]:
    if not columns and not allow_empty:
        raise ValueError("insert-from-select requires at least one target column")
    names: list[str] = []
    for column in columns:
        raw_column: object = column
        if not isinstance(column, Column):
            raise TypeError("target and conflict arguments must be table columns")
        node = expression_node(_expression(raw_column))
        if not isinstance(node, ColumnNode):
            raise TypeError("target and conflict arguments must be table columns")
        if node.source != table.reference or node.name not in table.column_names():
            raise ValueError("target and conflict columns must belong to the INSERT table")
        names.append(node.name)
    if len(names) != len(set(names)):
        raise ValueError("target and conflict columns cannot contain duplicates")
    return tuple(names)


Row_co = TypeVar("Row_co", covariant=True)
Returns = TypeVar("Returns", Literal[False], Literal[True])
Bounded = TypeVar("Bounded", Literal[False], Literal[True])


def _expression(value: object) -> Expression:
    if not isinstance(value, Expression):
        raise TypeError("expected a SQL expression")
    return value


@dataclass(frozen=True, slots=True, init=False)
class _DmlQuery(Query[Row_co], Generic[Row_co]):
    _table: Table[object]

    def with_node(self, node: InsertNode | UpdateNode | DeleteNode) -> Self:
        return new_query(type(self), node, extract_query(self).adapter, table=self._table)


@dataclass(frozen=True, slots=True, init=False)
class InsertQuery(_DmlQuery[Row_co], Generic[Row_co, Returns]):
    @property
    def _node(self) -> InsertNode:
        node = extract_query(self).node
        if not isinstance(node, InsertNode):  # pragma: no cover - factory invariant
            raise TypeError("INSERT query has a non-INSERT node")
        return node

    def _with_source(self, source: InsertSourceNode) -> InsertQuery[Row_co, Returns]:
        if self._node.source is not None:
            raise ValueError("an INSERT source can only be specified once")
        return self.with_node(replace(self._node, source=source))

    def values(self, **entries: object) -> InsertQuery[Row_co, Returns]:
        return self._with_source(InsertValuesSourceNode(_values(self._table, entries)))

    def values_many(self, rows: Iterable[Mapping[str, object]]) -> InsertQuery[Row_co, Returns]:
        """Insert equally shaped rows as one parameterized SQL statement."""
        prepared = tuple(_values(self._table, dict(row)) for row in rows)
        if not prepared:
            raise ValueError("values_many requires at least one row")
        columns = tuple(name for name, _ in prepared[0])
        if any(tuple(name for name, _ in row) != columns for row in prepared[1:]):
            raise ValueError(
                "every values_many row must contain the same columns in the same order"
            )
        return self._with_source(InsertRowsSourceNode(prepared))

    @overload
    def from_select[A, Result](
        self, query: SelectQuery[tuple[A], Result], first: Column[A], /
    ) -> InsertQuery[Row_co, Returns]: ...

    @overload
    def from_select[A, B, Result](
        self, query: SelectQuery[tuple[A, B], Result], first: Column[A], second: Column[B], /
    ) -> InsertQuery[Row_co, Returns]: ...

    @overload
    def from_select[A, B, C, Result](
        self,
        query: SelectQuery[tuple[A, B, C], Result],
        first: Column[A],
        second: Column[B],
        third: Column[C],
        /,
    ) -> InsertQuery[Row_co, Returns]: ...

    @overload
    def from_select[A, B, C, D, Result](
        self,
        query: SelectQuery[tuple[A, B, C, D], Result],
        first: Column[A],
        second: Column[B],
        third: Column[C],
        fourth: Column[D],
        /,
    ) -> InsertQuery[Row_co, Returns]: ...

    def from_select(
        self, query: SelectQuery[tuple[object, ...], object], *columns: object
    ) -> InsertQuery[Row_co, Returns]:
        names = _target_column_names(self._table, columns)
        node = select_node(query)
        if len(node.selections) != len(names):
            raise ValueError(
                "insert-from-select requires one target column per projected expression: "
                f"targets={len(names)}, projection={len(node.selections)}"
            )
        return self._with_source(InsertSelectSourceNode(node, names))

    def default_values(self) -> InsertQuery[Row_co, Returns]:
        """Insert one row using the table's declared defaults."""
        return self._with_source(DefaultValuesSourceNode())

    @overload
    def on_conflict(self) -> ConflictBuilder[Row_co, Returns]: ...

    @overload
    def on_conflict[A](self, first: Column[A], /) -> ConflictBuilder[Row_co, Returns]: ...

    @overload
    def on_conflict[A, B](
        self, first: Column[A], second: Column[B], /
    ) -> ConflictBuilder[Row_co, Returns]: ...

    @overload
    def on_conflict[A, B, C](
        self, first: Column[A], second: Column[B], third: Column[C], /
    ) -> ConflictBuilder[Row_co, Returns]: ...

    @overload
    def on_conflict[A, B, C, D](
        self, first: Column[A], second: Column[B], third: Column[C], fourth: Column[D], /
    ) -> ConflictBuilder[Row_co, Returns]: ...

    def on_conflict(self, *columns: object) -> ConflictBuilder[Row_co, Returns]:
        """Start an SQLite/PostgreSQL ``ON CONFLICT`` clause.

        One type parameter per position, like ``from_select``: a composite
        conflict target routinely mixes nullable and non-nullable columns, and
        ``Column`` is invariant, so a single shared parameter could not
        describe ``(queue, dedupe_key)``.
        """
        if self._node.source is None:
            raise ValueError("on_conflict requires values() or from_select() first")
        match self._node.source:
            case DefaultValuesSourceNode():
                raise ValueError(
                    "default-values inserts and conflict clauses are mutually exclusive"
                )
            case _:
                pass
        return ConflictBuilder(
            self, _target_column_names(self._table, columns, allow_empty=True), self._table
        )

    @overload
    def returning[A](self, first: Expr[A]) -> InsertQuery[tuple[A], Literal[True]]: ...

    @overload
    def returning[A, B](
        self, first: Expr[A], second: Expr[B]
    ) -> InsertQuery[tuple[A, B], Literal[True]]: ...

    @overload
    def returning[A, B, C](
        self, first: Expr[A], second: Expr[B], third: Expr[C]
    ) -> InsertQuery[tuple[A, B, C], Literal[True]]: ...

    @overload
    def returning[A, B, C, D](
        self, first: Expr[A], second: Expr[B], third: Expr[C], fourth: Expr[D]
    ) -> InsertQuery[tuple[A, B, C, D], Literal[True]]: ...

    @overload
    def returning[A, B, C, D, E](
        self, first: Expr[A], second: Expr[B], third: Expr[C], fourth: Expr[D], fifth: Expr[E]
    ) -> InsertQuery[tuple[A, B, C, D, E], Literal[True]]: ...

    @overload
    def returning[A, B, C, D, E, F](
        self,
        first: Expr[A],
        second: Expr[B],
        third: Expr[C],
        fourth: Expr[D],
        fifth: Expr[E],
        sixth: Expr[F],
    ) -> InsertQuery[tuple[A, B, C, D, E, F], Literal[True]]: ...

    @overload
    def returning[A, B, C, D, E, F, G](
        self,
        first: Expr[A],
        second: Expr[B],
        third: Expr[C],
        fourth: Expr[D],
        fifth: Expr[E],
        sixth: Expr[F],
        seventh: Expr[G],
    ) -> InsertQuery[tuple[A, B, C, D, E, F, G], Literal[True]]: ...

    @overload
    def returning[A, B, C, D, E, F, G, H](
        self,
        first: Expr[A],
        second: Expr[B],
        third: Expr[C],
        fourth: Expr[D],
        fifth: Expr[E],
        sixth: Expr[F],
        seventh: Expr[G],
        eighth: Expr[H],
    ) -> InsertQuery[tuple[A, B, C, D, E, F, G, H], Literal[True]]: ...

    def returning(
        self, first: object, *rest: object, **named: object
    ) -> InsertQuery[tuple[object, ...], Literal[True]]:
        if self._node.returning:
            raise ValueError("returning() can only be specified once")
        if named:
            raise TypeError("returning expressions must be positional")
        expressions = (first, *rest)
        if len(expressions) > 8:
            raise ValueError("returning supports at most eight expressions")
        nodes: list[Node] = []
        for expression in expressions:
            if not isinstance(expression, Expression):
                raise TypeError("returning accepts only SQL expressions")
            nodes.append(expression_node(expression))
        return new_query(
            InsertQuery, replace(self._node, returning=tuple(nodes)), table=self._table
        )

    def decode[Model](
        self: InsertQuery[Row_co, Literal[True]], model: type[Model] | RowAdapter[Model]
    ) -> InsertQuery[Model, Literal[True]]:
        adapter = model if isinstance(model, RowAdapter) else row_adapter(model)
        if len(self._node.returning) != adapter.arity:
            raise ValueError(f"{adapter.model_name} does not match RETURNING width")
        return new_query(InsertQuery, self._node, adapter, table=self._table)


@dataclass(frozen=True, slots=True)
class ConflictBuilder(Generic[Row_co, Returns]):
    """Incomplete INSERT conflict clause. It must end in an action."""

    _query: InsertQuery[Row_co, Returns]
    _columns: tuple[str, ...]
    _table: Table
    _target_where: Node | None = None

    def where(self, predicate: BooleanExpression) -> ConflictBuilder[Row_co, Returns]:
        """Match a partial unique index by repeating its own index predicate.

        This is the arbiter's ``WHERE``, not the action's: it selects which
        unique index PostgreSQL infers, and is required when that index is
        partial.  ``do_update(...).where(...)`` is the separate predicate that
        decides whether the update runs.
        """
        if not self._columns:
            raise ValueError("ON CONFLICT ... WHERE requires at least one conflict target column")
        if self._target_where is not None:
            raise ValueError("on_conflict().where() can only be specified once")
        return replace(self, _target_where=node_of(predicate))

    def do_nothing(self) -> InsertQuery[Row_co, Returns]:
        node = _insert_node(self._query)
        return self._query.with_node(
            replace(
                node,
                conflict=ConflictNode(self._columns, "nothing", target_where=self._target_where),
            )
        )

    def do_update(self, **entries: object) -> ConflictUpdateQuery[Row_co, Returns]:
        if not self._columns:
            raise ValueError("ON CONFLICT DO UPDATE requires at least one conflict target column")
        node = _insert_node(self._query)
        return new_query(
            ConflictUpdateQuery,
            replace(
                node,
                conflict=ConflictNode(
                    self._columns,
                    "update",
                    _values(self._table, entries),
                    target_where=self._target_where,
                ),
            ),
            extract_query(self._query).adapter,
            table=self._table,
        )


@dataclass(frozen=True, slots=True, init=False)
class ConflictUpdateQuery(InsertQuery[Row_co, Returns], Generic[Row_co, Returns]):
    """A complete INSERT whose ``DO UPDATE`` can still be narrowed by a predicate.

    It is already executable; ``where()`` is the optional compare-and-swap
    step, and must come before ``returning()`` because the wider builder
    methods deliberately return a plain ``InsertQuery``.
    """

    def where(self, predicate: BooleanExpression) -> InsertQuery[Row_co, Returns]:
        """Run the conflicting row's update only where ``predicate`` holds."""
        node = self._node
        conflict = node.conflict
        if conflict is None:  # pragma: no cover - construction invariant
            raise TypeError("expected an INSERT carrying a conflict clause")
        return new_query(
            InsertQuery,
            replace(node, conflict=replace(conflict, update_where=node_of(predicate))),
            extract_query(self).adapter,
            table=self._table,
        )


@dataclass(frozen=True, slots=True, init=False)
class UpdateQuery(_DmlQuery[Row_co], Generic[Row_co, Returns, Bounded]):
    @property
    def _node(self) -> UpdateNode:
        node = extract_query(self).node
        if not isinstance(node, UpdateNode):  # pragma: no cover - factory invariant
            raise TypeError("UPDATE query has a non-UPDATE node")
        return node

    def values(self, **entries: object) -> UpdateQuery[Row_co, Returns, Bounded]:
        if self._node.values:
            raise ValueError("values() can only be specified once")
        return self.with_node(replace(self._node, values=_values(self._table, entries)))

    def where(self, predicate: BooleanExpression) -> UpdateQuery[Row_co, Returns, Literal[True]]:
        return new_query(
            UpdateQuery,
            replace(self._node, where=node_of(predicate), bounded=True),
            extract_query(self).adapter,
            table=self._table,
        )

    def all_rows(self) -> UpdateQuery[Row_co, Returns, Literal[True]]:
        """Explicitly authorize a full-table update."""
        return new_query(
            UpdateQuery,
            replace(self._node, bounded=True),
            extract_query(self).adapter,
            table=self._table,
        )

    @overload
    def returning[A](self, first: Expr[A]) -> UpdateQuery[tuple[A], Literal[True], Bounded]: ...

    @overload
    def returning[A, B](
        self, first: Expr[A], second: Expr[B]
    ) -> UpdateQuery[tuple[A, B], Literal[True], Bounded]: ...

    @overload
    def returning[A, B, C](
        self, first: Expr[A], second: Expr[B], third: Expr[C]
    ) -> UpdateQuery[tuple[A, B, C], Literal[True], Bounded]: ...

    @overload
    def returning[A, B, C, D](
        self, first: Expr[A], second: Expr[B], third: Expr[C], fourth: Expr[D]
    ) -> UpdateQuery[tuple[A, B, C, D], Literal[True], Bounded]: ...

    @overload
    def returning[A, B, C, D, E](
        self, first: Expr[A], second: Expr[B], third: Expr[C], fourth: Expr[D], fifth: Expr[E]
    ) -> UpdateQuery[tuple[A, B, C, D, E], Literal[True], Bounded]: ...

    @overload
    def returning[A, B, C, D, E, F](
        self,
        first: Expr[A],
        second: Expr[B],
        third: Expr[C],
        fourth: Expr[D],
        fifth: Expr[E],
        sixth: Expr[F],
    ) -> UpdateQuery[tuple[A, B, C, D, E, F], Literal[True], Bounded]: ...

    @overload
    def returning[A, B, C, D, E, F, G](
        self,
        first: Expr[A],
        second: Expr[B],
        third: Expr[C],
        fourth: Expr[D],
        fifth: Expr[E],
        sixth: Expr[F],
        seventh: Expr[G],
    ) -> UpdateQuery[tuple[A, B, C, D, E, F, G], Literal[True], Bounded]: ...

    @overload
    def returning[A, B, C, D, E, F, G, H](
        self,
        first: Expr[A],
        second: Expr[B],
        third: Expr[C],
        fourth: Expr[D],
        fifth: Expr[E],
        sixth: Expr[F],
        seventh: Expr[G],
        eighth: Expr[H],
    ) -> UpdateQuery[tuple[A, B, C, D, E, F, G, H], Literal[True], Bounded]: ...

    def returning(
        self, first: object, *rest: object, **named: object
    ) -> UpdateQuery[tuple[object, ...], Literal[True], Bounded]:
        if self._node.returning:
            raise ValueError("returning() can only be specified once")
        if named:
            raise TypeError("returning expressions must be positional")
        expressions = (first, *rest)
        if len(expressions) > 8:
            raise ValueError("returning supports at most eight expressions")
        nodes: list[Node] = []
        for expression in expressions:
            if not isinstance(expression, Expression):
                raise TypeError("returning accepts only SQL expressions")
            nodes.append(expression_node(expression))
        return new_query(
            UpdateQuery, replace(self._node, returning=tuple(nodes)), table=self._table
        )

    def decode[Model](
        self: UpdateQuery[Row_co, Literal[True], Bounded],
        model: type[Model] | RowAdapter[Model],
    ) -> UpdateQuery[Model, Literal[True], Bounded]:
        adapter = model if isinstance(model, RowAdapter) else row_adapter(model)
        if len(self._node.returning) != adapter.arity:
            raise ValueError(f"{adapter.model_name} does not match RETURNING width")
        return new_query(UpdateQuery, self._node, adapter, table=self._table)


@dataclass(frozen=True, slots=True, init=False)
class DeleteQuery(_DmlQuery[Row_co], Generic[Row_co, Returns, Bounded]):
    @property
    def _node(self) -> DeleteNode:
        node = extract_query(self).node
        if not isinstance(node, DeleteNode):  # pragma: no cover - factory invariant
            raise TypeError("DELETE query has a non-DELETE node")
        return node

    def where(self, predicate: BooleanExpression) -> DeleteQuery[Row_co, Returns, Literal[True]]:
        return new_query(
            DeleteQuery,
            replace(self._node, where=node_of(predicate), bounded=True),
            extract_query(self).adapter,
            table=self._table,
        )

    def all_rows(self) -> DeleteQuery[Row_co, Returns, Literal[True]]:
        """Explicitly authorize a full-table delete."""
        return new_query(
            DeleteQuery,
            replace(self._node, bounded=True),
            extract_query(self).adapter,
            table=self._table,
        )

    @overload
    def returning[A](self, first: Expr[A]) -> DeleteQuery[tuple[A], Literal[True], Bounded]: ...

    @overload
    def returning[A, B](
        self, first: Expr[A], second: Expr[B]
    ) -> DeleteQuery[tuple[A, B], Literal[True], Bounded]: ...

    @overload
    def returning[A, B, C](
        self, first: Expr[A], second: Expr[B], third: Expr[C]
    ) -> DeleteQuery[tuple[A, B, C], Literal[True], Bounded]: ...

    @overload
    def returning[A, B, C, D](
        self, first: Expr[A], second: Expr[B], third: Expr[C], fourth: Expr[D]
    ) -> DeleteQuery[tuple[A, B, C, D], Literal[True], Bounded]: ...

    @overload
    def returning[A, B, C, D, E](
        self, first: Expr[A], second: Expr[B], third: Expr[C], fourth: Expr[D], fifth: Expr[E]
    ) -> DeleteQuery[tuple[A, B, C, D, E], Literal[True], Bounded]: ...

    @overload
    def returning[A, B, C, D, E, F](
        self,
        first: Expr[A],
        second: Expr[B],
        third: Expr[C],
        fourth: Expr[D],
        fifth: Expr[E],
        sixth: Expr[F],
    ) -> DeleteQuery[tuple[A, B, C, D, E, F], Literal[True], Bounded]: ...

    @overload
    def returning[A, B, C, D, E, F, G](
        self,
        first: Expr[A],
        second: Expr[B],
        third: Expr[C],
        fourth: Expr[D],
        fifth: Expr[E],
        sixth: Expr[F],
        seventh: Expr[G],
    ) -> DeleteQuery[tuple[A, B, C, D, E, F, G], Literal[True], Bounded]: ...

    @overload
    def returning[A, B, C, D, E, F, G, H](
        self,
        first: Expr[A],
        second: Expr[B],
        third: Expr[C],
        fourth: Expr[D],
        fifth: Expr[E],
        sixth: Expr[F],
        seventh: Expr[G],
        eighth: Expr[H],
    ) -> DeleteQuery[tuple[A, B, C, D, E, F, G, H], Literal[True], Bounded]: ...

    def returning(
        self, first: object, *rest: object, **named: object
    ) -> DeleteQuery[tuple[object, ...], Literal[True], Bounded]:
        if self._node.returning:
            raise ValueError("returning() can only be specified once")
        if named:
            raise TypeError("returning expressions must be positional")
        expressions = (first, *rest)
        if len(expressions) > 8:
            raise ValueError("returning supports at most eight expressions")
        nodes: list[Node] = []
        for expression in expressions:
            if not isinstance(expression, Expression):
                raise TypeError("returning accepts only SQL expressions")
            nodes.append(expression_node(expression))
        return new_query(
            DeleteQuery, replace(self._node, returning=tuple(nodes)), table=self._table
        )

    def decode[Model](
        self: DeleteQuery[Row_co, Literal[True], Bounded],
        model: type[Model] | RowAdapter[Model],
    ) -> DeleteQuery[Model, Literal[True], Bounded]:
        adapter = model if isinstance(model, RowAdapter) else row_adapter(model)
        if len(self._node.returning) != adapter.arity:
            raise ValueError(f"{adapter.model_name} does not match RETURNING width")
        return new_query(DeleteQuery, self._node, adapter, table=self._table)


def insert_into(table: Table[object]) -> InsertQuery[tuple[()], Literal[False]]:
    return new_query(InsertQuery, InsertNode(table_node(table)), table=table)


def update(table: Table[object]) -> UpdateQuery[tuple[()], Literal[False], Literal[False]]:
    return new_query(UpdateQuery, UpdateNode(table_node(table), ()), table=table)


def delete_from(table: Table[object]) -> DeleteQuery[tuple[()], Literal[False], Literal[False]]:
    return new_query(DeleteQuery, DeleteNode(table_node(table)), table=table)


def _insert_node[Returns: (Literal[False], Literal[True])](
    query: InsertQuery[object, Returns],
) -> InsertNode:
    node = extract_query(query).node
    if not isinstance(node, InsertNode):  # pragma: no cover - structural invariant
        raise TypeError("expected an INSERT query")
    return node
