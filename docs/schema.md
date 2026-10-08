# Schema

A relation's shape is declared as a class. [relq-codegen](./codegen.md) writes
these classes from an existing database.

## Tables

```python
from relq import Column, Table, column


class Users(Table):
    id: Column[int] = column()
    email: Column[str] = column()
    manager_id: Column[int | None] = column()


users = Users("users")
```

The annotation is the column's Python result type, and `column()` takes it from
there. Nullability belongs in the annotation, as in `Column[int | None]`.
`users.id` is a `Column[int]`. A column without an annotation is a type error.
Pass `name="..."` when the SQL column name differs from the Python attribute.

Any annotation works, including the recursive `JsonValue` alias for a `json` or
`jsonb` column: `payload: Column[JsonValue] = column()`.

A column cannot use an attribute name that relq uses on a relation, such as
`table_name`, `reference`, `node`, `as_`, or `_schema`. relq raises `TypeError`
when the class is defined, including for columns declared in a shared mixin.
Rename the attribute and keep the SQL name with `column(name="_schema")`.
`relq-codegen` applies the same rename.

## Schema-qualified tables

A table outside the connection's default namespace names its schema:

```python
queue_jobs = QueueJobs("jobs", schema="rqueue")
compute_jobs = ComputeJobs("jobs", schema="compute")
```

The schema compiles to its own quoted identifier, `"rqueue"."jobs"`. It applies
to `SELECT` sources and DML targets alike. The correlation name is the bare
table name, so columns render as `"jobs"."id"`. Two tables with the same name in
different schemas need `.as_(...)` aliases, as SQL requires.

Schemas are PostgreSQL-only. Compiling a schema-qualified table for SQLite
raises an error, because SQLite's qualified names address attached databases.

## Aliases and self-joins

`.as_("name")` returns a copy of the same class bound to a SQL alias. The column
types are unchanged:

```python
manager = users.as_("manager")
# manager.id is still Column[int]

query = (
    select(users.id, manager.email)
    .from_(users)
    .inner_join(manager, on=users.manager_id.eq(manager.id))
)
```

## Derived tables and CTEs

A typed relation over a query's output is a `DerivedTable` or `CteTable`
subclass with `output_column` fields:

```python
from relq import DerivedTable, count, output_column, select


class Totals(DerivedTable):
    manager_id: Column[int | None] = output_column()
    reports: Column[int] = output_column()


totals = (
    select(users.manager_id, count().as_("reports"))
    .from_(users)
    .group_by(users.manager_id)
    .as_(Totals, "totals")
)
```

`query.as_(Totals, "totals")` binds a declared relation to a projection.
`cte(Active, "active")` gives a typed CTE source for `.with_(...)`. Both check
that the declared output names match the query's selected column or alias names
before the query compiles. `totals.reports` is then a `Column[int]` that works
anywhere a column does.
