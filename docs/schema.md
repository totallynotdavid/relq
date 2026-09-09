# Schema

A relation's shape is declared as a class, never assembled from runtime strings.

## Tables

```python
from relq import Column, Table, column


class Users(Table):
    id: Column[int] = column(int)
    email: Column[str] = column(str)
    manager_id: Column[int | None] = column(int)


users = Users("users")
```

`column(python_type)` binds the column's Python result type; nullability lives
in the annotation (`Column[int | None]`), not in separate runtime metadata.
`Column[T]` is a typed descriptor: `users.id` is `Column[int]`. Pass
`name="..."` when the SQL column name differs from the Python attribute.

A declared column cannot take an attribute name relq itself uses on a relation
(`table_name`, `reference`, `node`, `as_`, `_schema`, ...); `Column` is a
non-data descriptor, so such an attribute would silently replace it. That is a
`TypeError` at class-definition time, and it covers a column a shared mixin
declares as well as one on the relation itself, because attribute lookup finds
both. Rename the attribute and keep the SQL name with
`column(str, name="_schema")`. `relq-codegen` applies the same rename
automatically.

## Schema-qualified tables

A table that lives outside the connection's default namespace declares the
schema that owns it:

```python
queue_jobs = QueueJobs("jobs", schema="rqueue")
compute_jobs = ComputeJobs("jobs", schema="compute")
```

The schema compiles to its own quoted identifier, `"rqueue"."jobs"`, never as
part of the table's name, and it applies to `SELECT` sources and DML targets
alike. The table's correlation name is still the bare table name, so columns
stay `"jobs"."id"`; two same-named tables in different schemas therefore need
`.as_(...)` aliases, exactly as SQL requires.

This is PostgreSQL-only. SQLite has qualified names too, but they address
attached databases rather than schemas, so compiling a schema-qualified table
for SQLite is a compile-time error instead of a query that quietly means
something else.

## Aliases and self-joins

`.as_("name")` returns a same-class, shallow copy bound to a SQL alias. Types
stay exact:

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

A reusable, typed relation over a query's output is a small `DerivedTable` or
`CteTable` subclass with `output_column` fields:

```python
from relq import DerivedTable, count, output_column, select


class Totals(DerivedTable):
    manager_id: Column[int | None] = output_column(int)
    reports: Column[int] = output_column(int)


totals = (
    select(users.manager_id, count().as_("reports"))
    .from_(users)
    .group_by(users.manager_id)
    .as_(Totals, "totals")
)
```

`query.as_(Totals, "totals")` binds a declared relation to a projection;
`cte(Active, "active")` gives a typed CTE source for use with `.with_(...)`.
Both validate that declared output names match the query's selected column or
alias names before the query compiles. `totals.reports` is now a typed
`Column[int]`, usable anywhere a normal column is, without redeclaring the
projection at every call site.

There is no dynamic `cte("name")`, `query.as_("alias")` as an untyped relation,
or `.column("name")` accessor. A relation's output schema always comes from a
declared class, so both the type checker and the compiler can validate against
it.

## Codegen-generated schema

`relq-codegen` generates `Table` declarations, plus DML payload contracts,
directly from database metadata, so schema modules don't have to be handwritten
and kept in sync by hand. See [Code generation](./codegen.md).
