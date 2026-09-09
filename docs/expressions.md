# Expressions

## Predicates and NULL

relq distinguishes two Boolean states, matching SQL's three-valued logic:

- `Predicate` (`bool`): `IS NULL`, `IS NOT NULL`, `IS TRUE`, `IS FALSE`,
  `IS NOT TRUE`, `IS NOT FALSE`, and `EXISTS` are always one of `TRUE`/`FALSE`.
- `NullablePredicate` (`bool | None`): comparisons, range and membership
  predicates, and `LIKE` are conservative. SQL comparisons can become `UNKNOWN`,
  and Python can't soundly prove an arbitrary operand is non-null.

`where`, `having`, join predicates, and aggregate filters accept either, because
SQL filtering discards both `FALSE` and `UNKNOWN` identically. Projecting a
comparison keeps its `bool | None` result; call `.is_true()` on it when a
selected, total Boolean is required.

## Comparisons and composition

`.eq`, `.ne`, `.lt`, `.lte`, `.gt`, `.gte`, `.in_`, `.not_in`, `.between`,
`.not_between`, and `.like` are the comparison surface; `&`, `|`, and `~`
compose Boolean expressions. Empty `.in_([])` reduces to a bound `FALSE` rather
than emitting invalid `IN ()` SQL.

## PostgreSQL-only expressions

`relq.postgres` holds the expressions only PostgreSQL has, as named typed
functions rather than an escape hatch:

```python
from relq.postgres import cast_uuid, json_text, regex_match

candidate = json_text(jobs.payload, "compute_job_id")  # ->>  Expr[str | None]
guarded = regex_match(candidate, UUID_RE, insensitive=True)  # ~*  NullablePredicate
job_id = cast_uuid(candidate)  # ::uuid  Expr[UUID | None]
```

`json_text` binds its member key as a parameter and returns an optional result,
because the member may be absent or JSON `null`. `regex_match` is a
`NullablePredicate` for the same reason `LIKE` is. `cast_uuid` keeps a non-null
argument's result non-null and preserves an optional one; PostgreSQL raises on
malformed text rather than producing `NULL`, so guard the value with
`regex_match` first.

Compiling any of these for SQLite is a compile-time error that names the
expression. See [Design boundaries](./design-boundaries.md).

## Conditional expressions

`case_when`, `coalesce`, and `nullif` are the complete, closed conditional
surface. There is no generic function-name builder or raw SQL fragment.

```python
state = (
    case_when(users.active.is_true(), "active")
    .when(users.email.is_null(), "missing-email")
    .else_("inactive")
)
```

`case_when` returns an immutable, incomplete builder. Each `.when(...)` adds a
same-typed branch, and `.else_(...)` is the only ordinary terminal. There's no
implicit no-`ELSE` form and no simple-CASE/operator-string grammar.
`.else_null()` is the explicit alternative; it changes the result type to
`T | None`. `coalesce` requires at least two same-typed expressions, matching
SQL. `nullif(a, b)` compiles straight to `NULLIF(a, b)`.

## Numeric operations

Numeric SQL uses typed functions (`add`, `subtract`, `multiply`, `divide`)
rather than overloaded Python arithmetic operators, so each dialect's actual
numeric behavior stays visible in the type:

```python
update(users).values(login_count=add(users.login_count, 1)).where(users.id.eq(42))
```

`divide(int, int)` is integer division on both engines; `divide(float, float)`
is floating point. PostgreSQL `numeric` decodes as `Decimal`, while SQLite's
type affinity can produce `int` or `float`, so Decimal and nullable arithmetic
expose an honest cross-dialect union rather than pretending one engine's
behavior applies everywhere. `avg` similarly returns `float | Decimal | None`.
Use a `RowAdapter` with `decimal_decoder()` when application code needs a
portable `Decimal`. See [Execution](./execution.md).
