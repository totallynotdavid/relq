"""Split a large row stream into statements that fit a dialect's parameter ceiling."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Literal

from relq._ast import InsertNode, InsertRowsSourceNode, QueryNode
from relq._compiler._model import Dialect
from relq._compiler._render import count_parameters
from relq._node_value import expression_node
from relq._query import extract_query
from relq.dml import InsertQuery
from relq.expressions.core import Expression

type Rows = tuple[Mapping[str, object], ...]


def batch_rows[Row, Returns: (Literal[False], Literal[True])](
    rows: Iterable[Mapping[str, object]],
    statement: Callable[[Rows], InsertQuery[Row, Returns]],
    *,
    dialect: Dialect,
    max_parameters: int,
) -> Iterator[InsertQuery[Row, Returns]]:
    """Yield ``statement`` over the largest consecutive batches that fit the ceiling.

    ``statement`` turns a batch of rows into the whole INSERT, so every parameter
    it binds counts. This includes the rows' parameters and those of
    ``on_conflict`` values and ``returning`` expressions. A plain value binds one
    parameter. An expression binds as many as its rendering does, which may be
    none. Every statement yielded compiles whenever its rows do.

    Rows are read lazily, one row ahead of the batch being filled. A row shape
    mismatch is reported by ``values_many`` when the offending batch is built.
    """
    batch: list[Mapping[str, object]] = []
    fixed: int | None = None
    used = 0
    for row in rows:
        cost = _row_parameters(row, dialect)
        if fixed is None:
            fixed = _fixed_parameters(extract_query(statement((row,))).node, cost, dialect)
            used = fixed
        if fixed + cost > max_parameters:
            raise ValueError(
                f"a statement of one row needs {fixed + cost} parameters, over the "
                f"{max_parameters}-parameter statement ceiling"
            )
        if used + cost > max_parameters:
            yield statement(tuple(batch))
            batch = []
            used = fixed
        batch.append(row)
        used += cost
    if batch:
        yield statement(tuple(batch))


def _fixed_parameters(node: QueryNode, row_cost: int, dialect: Dialect) -> int:
    """Count the parameters a one-row statement binds besides its row."""
    if not (
        isinstance(node, InsertNode)
        and isinstance(node.source, InsertRowsSourceNode)
        and len(node.source.rows) == 1
    ):
        raise TypeError("the statement must insert exactly the rows it is given with values_many")
    return count_parameters(node, dialect) - row_cost


def _row_parameters(row: Mapping[str, object], dialect: Dialect) -> int:
    if not row:
        raise ValueError("a row must contain at least one column")
    return sum(_cell_parameters(cell, dialect) for cell in row.values())


def _cell_parameters(cell: object, dialect: Dialect) -> int:
    if isinstance(cell, Expression):
        return count_parameters(expression_node(cell), dialect)
    return 1
