"""Private immutable SQL syntax tree types.

Builders only construct these values.  Rendering and validation live in the
compiler, keeping the public query objects small and dialect-independent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class TableSourceNode:
    name: str
    alias: str | None = None

    @property
    def reference(self) -> str:
        return self.alias or self.name


@dataclass(frozen=True, slots=True)
class DerivedSourceNode:
    query: SelectNode
    alias: str

    @property
    def reference(self) -> str:
        return self.alias


@dataclass(frozen=True, slots=True)
class CteSourceNode:
    name: str

    @property
    def reference(self) -> str:
        return self.name


type SourceNode = TableSourceNode | DerivedSourceNode | CteSourceNode


@dataclass(frozen=True, slots=True)
class ColumnNode:
    source: str
    name: str


@dataclass(frozen=True, slots=True)
class ValueNode:
    value: object


@dataclass(frozen=True, slots=True)
class TemporalClockNode:
    kind: Literal[
        "transaction_timestamp",
        "statement_timestamp",
        "clock_timestamp",
        "current_date",
        "current_time",
        "local_time",
        "local_timestamp",
    ]


@dataclass(frozen=True, slots=True)
class BinaryNode:
    left: Node
    operator: str
    right: Node


@dataclass(frozen=True, slots=True)
class TemporalMakeDateNode:
    year: Node
    month: Node
    day: Node


@dataclass(frozen=True, slots=True)
class TemporalMakeTimeNode:
    hour: Node
    minute: Node
    second: Node


@dataclass(frozen=True, slots=True)
class TemporalMakeTimestampNode:
    year: Node
    month: Node
    day: Node
    hour: Node
    minute: Node
    second: Node


@dataclass(frozen=True, slots=True)
class TemporalMakeTimestamptzNode:
    year: Node
    month: Node
    day: Node
    hour: Node
    minute: Node
    second: Node
    zone: Node | None = None


@dataclass(frozen=True, slots=True)
class TemporalMakeIntervalNode:
    """Named PostgreSQL ``make_interval`` components."""

    components: tuple[tuple[str, Node], ...]


@dataclass(frozen=True, slots=True)
class TemporalEpochNode:
    seconds: Node


@dataclass(frozen=True, slots=True)
class TemporalArithmeticNode:
    timestamp: Node
    operator: Literal["+", "-"]
    interval: Node


@dataclass(frozen=True, slots=True)
class TemporalDifferenceNode:
    left: Node
    right: Node


@dataclass(frozen=True, slots=True)
class TemporalIntervalUnaryNode:
    interval: Node


@dataclass(frozen=True, slots=True)
class TemporalIntervalScaleNode:
    interval: Node
    operator: Literal["*", "/"]
    factor: Node


@dataclass(frozen=True, slots=True)
class TemporalTimezoneNode:
    expression: Node
    zone: Node


@dataclass(frozen=True, slots=True)
class TemporalExtractNode:
    field: str
    expression: Node


@dataclass(frozen=True, slots=True)
class TemporalTruncNode:
    unit: str
    expression: Node


@dataclass(frozen=True, slots=True)
class TemporalTruncTimestamptzNode:
    unit: str
    expression: Node
    zone: Node


@dataclass(frozen=True, slots=True)
class TemporalBinNode:
    stride: Node
    expression: Node
    origin: Node


@dataclass(frozen=True, slots=True)
class TemporalAgeNode:
    left: Node
    right: Node


@dataclass(frozen=True, slots=True)
class TemporalOverlapsNode:
    left_start: Node
    left_end: Node
    right_start: Node
    right_end: Node


@dataclass(frozen=True, slots=True)
class TemporalJustifyNode:
    kind: Literal["justify_days", "justify_hours", "justify_interval"]
    interval: Node


@dataclass(frozen=True, slots=True)
class UnaryNode:
    operator: str
    operand: Node


@dataclass(frozen=True, slots=True)
class FunctionNode:
    name: str
    arguments: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class CaseNode:
    """A closed searched-CASE expression; no SQL fragments are accepted."""

    branches: tuple[tuple[Node, Node], ...]
    otherwise: Node


@dataclass(frozen=True, slots=True)
class AggregateNode:
    """A portable aggregate call.

    It has its own node type so only supported aggregate expressions can be
    turned into window expressions. Generic SQL functions deliberately cannot.
    """

    name: str
    arguments: tuple[Node, ...]
    filter: Node | None = None


@dataclass(frozen=True, slots=True)
class WindowNode:
    expression: AggregateNode | FunctionNode
    partition_by: tuple[Node, ...] = ()
    order_by: tuple[OrderNode, ...] = ()
    frame: WindowFrameNode | None = None
    exclusion: WindowExclusion | None = None


@dataclass(frozen=True, slots=True)
class FrameBoundaryNode:
    """A validated SQL window-frame boundary.

    ``amount`` is present only for ``preceding`` and ``following``.  Keeping
    the small closed representation in the AST means frame offsets never pass
    through a string-based SQL escape hatch.
    """

    kind: str
    amount: int | None = None


@dataclass(frozen=True, slots=True)
class WindowFrameNode:
    kind: str
    start: FrameBoundaryNode
    end: FrameBoundaryNode


type WindowExclusion = Literal["no others", "current row", "group", "ties"]
type NullPlacement = Literal["first", "last"]


@dataclass(frozen=True, slots=True)
class AliasNode:
    expression: Node
    alias: str


@dataclass(frozen=True, slots=True)
class NullableResultNode:
    """A type-only marker for a result that SQL may NULL-extend.

    The renderer deliberately erases this node.  It exists so validation can
    require an explicit public acknowledgement when an outer join changes an
    expression's result domain without changing its SQL spelling.
    """

    expression: Node


@dataclass(frozen=True, slots=True)
class StarNode:
    pass


@dataclass(frozen=True, slots=True)
class InNode:
    expression: Node
    values: tuple[Node, ...] | SelectNode
    negated: bool = False


@dataclass(frozen=True, slots=True)
class BetweenNode:
    expression: Node
    lower: Node
    upper: Node
    negated: bool = False


@dataclass(frozen=True, slots=True)
class ScalarSubqueryNode:
    query: SelectNode


@dataclass(frozen=True, slots=True)
class ExistsNode:
    query: SelectNode
    negated: bool = False


@dataclass(frozen=True, slots=True)
class ExcludedNode:
    """A proposed row column in an INSERT conflict update."""

    source: str
    name: str


@dataclass(frozen=True, slots=True)
class OrderNode:
    expression: Node
    direction: Literal["asc", "desc"]
    nulls: NullPlacement | None = None


@dataclass(frozen=True, slots=True)
class JoinNode:
    source: SourceNode
    predicate: Node
    kind: str


@dataclass(frozen=True, slots=True)
class CteNode:
    name: str
    query: SelectNode
    recursive: bool = False


@dataclass(frozen=True, slots=True)
class CompoundNode:
    operator: CompoundOperator
    query: SelectNode


@dataclass(frozen=True, slots=True)
class LockClauseNode:
    strength: Literal["update", "no key update", "share", "key share"]
    of: tuple[TableSourceNode, ...] = ()
    wait: Literal["nowait", "skip locked"] | None = None


@dataclass(frozen=True, slots=True)
class SelectNode:
    selections: tuple[Node, ...]
    from_source: SourceNode | None = None
    joins: tuple[JoinNode, ...] = ()
    where: Node | None = None
    group_by: tuple[Node, ...] = ()
    having: Node | None = None
    order_by: tuple[OrderNode, ...] = ()
    limit: int | None = None
    offset: int | None = None
    distinct: bool = False
    ctes: tuple[CteNode, ...] = ()
    compounds: tuple[CompoundNode, ...] = ()
    locks: tuple[LockClauseNode, ...] = ()


type CompoundOperator = Literal["union", "union all", "intersect", "except"]


@dataclass(frozen=True, slots=True)
class InsertValuesSourceNode:
    values: tuple[tuple[str, Node], ...]


@dataclass(frozen=True, slots=True)
class InsertRowsSourceNode:
    rows: tuple[tuple[tuple[str, Node], ...], ...]


@dataclass(frozen=True, slots=True)
class InsertSelectSourceNode:
    query: SelectNode
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DefaultValuesSourceNode:
    pass


type InsertSourceNode = (
    InsertValuesSourceNode | InsertRowsSourceNode | InsertSelectSourceNode | DefaultValuesSourceNode
)


@dataclass(frozen=True, slots=True)
class InsertNode:
    table: TableSourceNode
    source: InsertSourceNode | None = None
    conflict: ConflictNode | None = None
    returning: tuple[Node, ...] = ()


@dataclass(frozen=True, slots=True)
class ConflictNode:
    columns: tuple[str, ...]
    action: str
    update_values: tuple[tuple[str, Node], ...] = ()


@dataclass(frozen=True, slots=True)
class UpdateNode:
    table: TableSourceNode
    values: tuple[tuple[str, Node], ...]
    where: Node | None = None
    bounded: bool = False
    returning: tuple[Node, ...] = ()


@dataclass(frozen=True, slots=True)
class DeleteNode:
    table: TableSourceNode
    where: Node | None = None
    bounded: bool = False
    returning: tuple[Node, ...] = ()


type Node = (
    ColumnNode
    | ValueNode
    | TemporalClockNode
    | BinaryNode
    | TemporalMakeDateNode
    | TemporalMakeTimeNode
    | TemporalMakeTimestampNode
    | TemporalMakeTimestamptzNode
    | TemporalMakeIntervalNode
    | TemporalEpochNode
    | TemporalArithmeticNode
    | TemporalDifferenceNode
    | TemporalIntervalUnaryNode
    | TemporalIntervalScaleNode
    | TemporalTimezoneNode
    | TemporalExtractNode
    | TemporalTruncNode
    | TemporalTruncTimestamptzNode
    | TemporalBinNode
    | TemporalAgeNode
    | TemporalOverlapsNode
    | TemporalJustifyNode
    | UnaryNode
    | FunctionNode
    | CaseNode
    | AggregateNode
    | WindowNode
    | AliasNode
    | NullableResultNode
    | StarNode
    | InNode
    | BetweenNode
    | ScalarSubqueryNode
    | ExistsNode
    | ExcludedNode
)
type QueryNode = SelectNode | InsertNode | UpdateNode | DeleteNode
