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

`column(python_type)` binds the column's Python result type. Nullability belongs
in the annotation, as in `Column[int | None]`. `Column[T]` is a typed
descriptor, so `users.id` is a `Column[int]`. Pass `name="..."` when the SQL
column name differs from the Python attribute.

A column cannot use an attribute name that relq uses on a relation, such as
`table_name`, `reference`, `node`, `as_`, or `_schema`. `Column` is a non-data
descriptor, so an instance attribute with the same name would replace it. relq
raises `TypeError` when the class is defined. The check covers columns declared
in a shared mixin as well, because attribute lookup finds them too. Rename the
attribute and keep the SQL name with `column(str, name="_schema")`.
`relq-codegen` applies the same rename automatically.

## Schema-qualified tables

A table that lives outside the connection's default namespace declares the
schema that owns it:

```python
queue_jobs = QueueJobs("jobs", schema="rqueue")
compute_jobs = ComputeJobs("jobs", schema="compute")
```

The schema compiles to its own quoted identifier, `"rqueue"."jobs"`, and never as
part of the table's name. It applies to `SELECT` sources and DML targets alike.
The correlation name is still the bare table name, so columns render as
`"jobs"."id"`. Two same-named tables in different schemas therefore need
`.as_(...)` aliases, as SQL requires.

Schemas are PostgreSQL-only. SQLite's qualified names address attached databases
instead of schemas, so compiling a schema-qualified table for SQLite raises an
error instead of producing a query with a different meaning.

## Aliases and self-joins

`.as_("name")` returns a shallow copy of the same class, bound to a SQL alias.
The column types are unchanged:

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

A typed, reusable relation over a query's output is a small `DerivedTable` or
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

`query.as_(Totals, "totals")` binds a declared relation to a projection.
`cte(Active, "active")` gives a typed CTE source for `.with_(...)`. Both check
that the declared output names match the query's selected column or alias names
before the query compiles. `totals.reports` is then a `Column[int]` that works
anywhere a normal column does.

There is no untyped `cte("name")`, no untyped `query.as_("alias")`, and no
`.column("name")` accessor. A relation's output schema always comes from a
declared class, so both the type checker and the compiler can validate against
it.

## Generated schemas

`relq-codegen` generates `Table` declarations and DML payload contracts from
database metadata, so nobody writes or updates schema modules by hand. See
[Code generation](./codegen.md).
