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

Joins are `inner_join`, `left_join`, `right_join`, `full_join`, and
`cross_join`. A join predicate is validated where it is introduced, so it cannot
reference a relation that appears later in the query.

## Outer joins and nullability

An outer join can produce `NULL` for a column that is non-null in its own table.
Projecting such a column requires `.nullable()`, which changes only the static
result type and renders no SQL:

```python
query = (
    select(users.id, manager.email.nullable())
    .from_(users)
    .left_join(manager, on=users.manager_id.eq(manager.id))
)
# SelectQuery[tuple[int, str | None]]
```

The compiler tracks which relations, aliases included, the join tree
NULL-extends. It rejects an unmarked column or arithmetic projection over one of
them.

## Subqueries

```python
latest_email = scalar(
    select(logs.email).from_(logs).where(logs.user_id.eq(users.id)).limit(1),
)
```

`scalar(query)` embeds a query as `Expr[T | None]`. The result includes `None`
because an empty subquery yields `NULL`. `exists(query)` and `not_exists(query)`
embed a query as a total `Predicate`. All three allow correlated references to
the outer query.

## Grouping

Once a query has `GROUP BY` or an aggregate, every non-aggregate local column in
`SELECT`, `HAVING`, top-level `ORDER BY`, or a window's partition or order must
be structurally covered by `GROUP BY`. The compiler checks this before
rendering, so a query that SQLite would accept with a bare column cannot fail
later on PostgreSQL. A correlated outer reference is constant within its own
grouping scope. `GROUP BY` takes the underlying typed expression and never a
`SELECT`-list alias, because relq does not model aliases as expressions.

## Set operations

```python
active_or_pending = users_query.union(pending_query)
```

`union`, `union_all`, `intersect`, and `except_` require both queries to share the
same raw row type. relq rejects mismatched projection widths before compilation.
`decode()` only attaches an executor-side decoder and never changes the SQL, so a
plain query and a decoded one, or two queries decoded into different models,
compound like any other pair. The compound keeps the left query's decoder, if it
has one, and drops the right query's.

A compound arm cannot carry `ORDER BY`, `LIMIT`, `OFFSET`, or a row-locking
clause. To order or paginate a compound, bind it to a declared `DerivedTable` and
apply those clauses to the outer query over its named output columns.

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

`.with_(source, query)` declares a non-recursive CTE. It can reference only CTEs
declared before it. Duplicate names and forward references are rejected before
SQL is rendered. `.with_recursive(source, query)` requires a non-recursive seed
combined with `union_all` and a recursive arm. Only the recursive arm may
reference the CTE itself.

### Materialization

`.with_(source, query, materialized=True)` emits `AS MATERIALIZED`, which stops
the planner from inlining the CTE into its references. Without it the CTE renders
as a plain `AS (...)` and the planner decides.

Both engines accept the modifier at relq's version floors. SQLite added it in
3.35.0, the release that also added `RETURNING`, and PostgreSQL added it in 12.
See [Engine versions](./installation.md#engine-versions).

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
once for the whole statement, whatever the outer query does with its rows. relq
therefore requires it to carry `RETURNING` and to be declared on the outermost
query. Nesting one inside a derived table, subquery, or another CTE is rejected
before rendering. Data-modifying CTEs are PostgreSQL-only, because SQLite has
none. Executing such a query changes data even though it goes through
`fetch_one`.

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

Forward references are still rejected. A CTE can only read names declared
before it.

### CTE bodies and row models

`with_` and `with_recursive` take the plain tuple builders (`select`,
`returning`), never a query that has been through `decode()`. A CTE's output
relation is declared by its `CteTable`, and the outer query owns the statement's
result shape, so an adapter on a CTE body would have nothing to decode. Passing
one raises at the builder, so a declared contract is never dropped silently. For
a `SELECT` body it is also a type error. Decode the outer query instead.
