# Expressions

## Predicates and NULL

relq distinguishes two Boolean types, matching SQL's three-valued logic:

- `Predicate` (`bool`) is always `TRUE` or `FALSE`. This covers `IS NULL`,
  `IS NOT NULL`, `IS TRUE`, `IS FALSE`, `IS NOT TRUE`, `IS NOT FALSE`, and
  `EXISTS`.
- `NullablePredicate` (`bool | None`) covers comparisons, range and membership
  predicates, and `LIKE`. These can be `UNKNOWN`, and the type checker cannot
  prove that an arbitrary operand is non-null.

`where`, `having`, join predicates, and aggregate filters accept either type,
because SQL filtering discards `FALSE` and `UNKNOWN` alike. A projected
comparison keeps its `bool | None` type. Call `.is_true()` on it to select a
total Boolean.

## Comparisons and composition

The comparison methods are `.eq`, `.ne`, `.lt`, `.lte`, `.gt`, `.gte`, `.in_`,
`.not_in`, `.between`, `.not_between`, and `.like`. The operators `&`, `|`, and
`~` combine Boolean expressions. An empty `.in_([])` reduces to a bound `FALSE`,
so relq never emits the invalid `IN ()`.

## PostgreSQL-only expressions

`relq.postgres` holds the expressions that only PostgreSQL has, written as named
typed functions:

```python
from relq.postgres import cast_uuid, json_text, regex_match

candidate = json_text(jobs.payload, "compute_job_id")  # ->>  Expr[str | None]
guarded = regex_match(candidate, UUID_RE, insensitive=True)  # ~*  NullablePredicate
job_id = cast_uuid(candidate)  # ::uuid  Expr[UUID | None]
```

`json_text` binds its member key as a parameter and returns an optional result,
because the member may be absent or JSON `null`. `regex_match` is a
`NullablePredicate` for the same reason `LIKE` is. `cast_uuid` returns a non-null
result for a non-null argument and an optional result for an optional one.
PostgreSQL raises on malformed text instead of returning `NULL`, so guard the
value with `regex_match` first.

Compiling any of these for SQLite raises an error that names the expression. See
[Design boundaries](./design-boundaries.md).

## Conditional expressions

`case_when`, `coalesce`, and `nullif` are the whole conditional surface. There is
no generic function-name builder or raw SQL fragment.

```python
state = (
    case_when(users.active.is_true(), "active")
    .when(users.email.is_null(), "missing-email")
    .else_("inactive")
)
```

`case_when` returns an immutable builder that is not yet a value. Each
`.when(...)` adds a branch of the same type. `.else_(...)` closes the expression.
`.else_null()` closes it with a `NULL` fallback and changes the result type to
`T | None`. There is no `CASE` without `ELSE` and no simple-`CASE` form.
`coalesce` requires at least two expressions of the same type. `nullif(a, b)`
compiles to `NULLIF(a, b)`.

## Numeric operations

Numeric SQL uses the typed functions `add`, `subtract`, `multiply`, and
`divide` instead of overloaded Python operators, so each dialect's numeric
behavior shows in the type:

```python
update(users).values(login_count=add(users.login_count, 1)).where(users.id.eq(42))
```

`divide(int, int)` is integer division on both engines. `divide(float, float)` is
floating point. PostgreSQL `numeric` decodes as `Decimal`, and SQLite's type
affinity can produce `int` or `float`. Decimal and nullable arithmetic therefore
return a union that is correct on both dialects, and `avg` returns
`float | Decimal | None`. Use a `RowAdapter` with `decimal_decoder()` when
application code needs a `Decimal` on both engines. See
[Execution](./execution.md).
