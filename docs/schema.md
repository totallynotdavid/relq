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

`column(...)` describes the SQL type and whether the column is nullable, a
primary key, and so on. `Column[T]` is a typed descriptor: `users.id` is
`Column[int]`.

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
