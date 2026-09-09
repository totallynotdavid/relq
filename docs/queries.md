# Queries

## Select and joins

```python
query = (
    select(users.id, users.email)
    .from_(users)
    .inner_join(orders, on=users.id.eq(orders.user_id))
    .where(users.active.is_true())
    .order_by(users.email.asc())
    .limit(50)
)
```

Joins are explicit: `inner_join`, `left_join`, `right_join`, `full_join`,
`cross_join`. Each join's predicate is validated as it's introduced, so it can't
reference a relation that only appears later in the query.

## Outer joins and nullability

An outer join can manufacture `NULL` for a column that's non-null in its own
table. Projecting such a column requires an explicit `.nullable()`, which
changes only the static result type and renders no SQL:

```python
query = (
    select(users.id, manager.email.nullable())
    .from_(users)
    .left_join(manager, on=users.manager_id.eq(manager.id))
)
# SelectQuery[tuple[int, str | None]]
```

The compiler tracks which relation identities (including aliases) are
NULL-extended by the join tree, and rejects an unmarked column or arithmetic
projection over one of them.

## Subqueries

```python
latest_email = scalar(
    select(logs.email).from_(logs).where(logs.user_id.eq(users.id)).limit(1),
)
```

`scalar(query)` embeds a query as `Expr[T | None]`. It's `T | None` because an
empty subquery yields SQL `NULL`. `exists(query)` and `not_exists(query)` embed
a query as a total `Predicate`. All three permit correlated references to the
outer query.

## Grouping

Once a query has `GROUP BY` or an aggregate, every non-aggregate local column in
`SELECT`, `HAVING`, top-level `ORDER BY`, or a window's partition/order must be
structurally covered by `GROUP BY`. This is checked before the query compiles,
so SQLite's permissive bare-column behavior can't slip through and then fail on
PostgreSQL. A correlated outer reference is constant within its own nested
grouping scope. relq does not model `SELECT`-list aliases as expressions, so
`GROUP BY` always takes the underlying typed expression, never a projected
alias.

## Set operations

```python
active_or_pending = users_query.union(pending_query)
```

`union`, `union_all`, `intersect`, and `except_` require both queries to share
the same declared row type; relq rejects mismatched projection widths before
compilation. A tuple query can't be compounded with a declared-model query, and
two model queries must share the same model and decoder. Compound arms can't
carry `ORDER BY`, `LIMIT`, or `OFFSET`. Bind the compound to a declared
`DerivedTable` and order or paginate the outer query over its named output
columns instead.

## Common table expressions

```python
class Active(CteTable):
    id: Column[int] = output_column(int)


active = cte(Active, "active")
query = (
    select(active.id, totals.reports)
    .from_(active)
    .inner_join(totals, on=active.id.eq(totals.manager_id))
    .with_(active, select(users.id).from_(users).where(users.active.is_true()))
)
```

`.with_(source, query)` declares a non-recursive CTE; it can reference only CTEs
declared before it, and duplicate names or forward references are rejected
before SQL is emitted. `.with_recursive(source, query)` requires a non-recursive
seed unioned (`union_all`) with a recursive arm; only that arm may reference the
CTE itself.

### Materialization

`.with_(source, query, materialized=True)` emits `AS MATERIALIZED`, the
optimizer fence that stops the planner from inlining the CTE into its
references. Leaving it unset emits a plain `AS (...)` and leaves the choice to
the planner.

Both engines accept the modifier at relq's documented version floors: SQLite
added it in 3.35.0, the same release that added `RETURNING`, so it costs SQLite
nothing relq did not already require; PostgreSQL added it in 12, which is what
sets relq's PostgreSQL floor. An older server rejects the statement itself; see
[Engine versions](./installation.md#engine-versions).

### Data-modifying CTEs

`.with_(...)` also accepts a bounded `INSERT`, `UPDATE`, or `DELETE` whose
`RETURNING` list becomes the CTE's output relation:

```python
class Removed(CteTable):
    id: Column[int] = output_column(int)


removed = cte(Removed, "removed")
purged = (
    select(count())
    .from_(removed)
    .with_(
        removed,
        delete_from(jobs).where(jobs.finished_at.lt(cutoff)).returning(jobs.id),
    )
)
```

The statement deletes and counts atomically. A data-modifying CTE runs exactly
once for the whole statement, whatever the outer query does with its rows, so
relq requires it to carry `RETURNING` and to be declared on the outermost query;
nesting one inside a derived table, subquery, or another CTE is rejected before
compilation. This is PostgreSQL-only (SQLite has no data-modifying CTE) and
executing such a query mutates data even though it goes through `fetch_one`.

Like a `SELECT` body, a data-modifying body sees the CTEs bound before it, so a
later CTE can consume an earlier one's `RETURNING` rows:

```python
query = (
    select(count())
    .from_(archived)
    .with_(removed, delete_from(jobs).where(jobs.finished_at.lt(cutoff)).returning(jobs.id))
    .with_(
        archived,
        insert_into(job_archive)
        .from_select(select(removed.id).from_(removed), job_archive.id)
        .returning(job_archive.id),
    )
)
```

Forward references are still rejected: a CTE can only read names declared
before it.

### CTE bodies and row models

`with_` and `with_recursive` take the tuple builders (`select`, `returning`),
never `select_model` / `returning_model`. A CTE's output relation is declared by
its `CteTable`, and the statement's result shape belongs to the outer query, so
an adapter attached to a CTE body would have nothing to decode. Passing one is a
type error, and a runtime error at the builder boundary, rather than a silently
dropped contract. Decode the outer query instead.
