# Dialects

relq compiles a query for one dialect, SQLite or PostgreSQL. Most of what the
top-level `relq` package exports compiles for both. The features below are
PostgreSQL-only. Compiling a query that uses one for SQLite raises `ValueError`
and names the unsupported feature, so a PostgreSQL query never turns into a
different SQLite query.

| Feature                                                   | Documented in                                                     |
| --------------------------------------------------------- | ----------------------------------------------------------------- |
| `relq.postgres`: `cast_uuid`, `json_text`, `regex_match`  | [Expressions](./expressions.md#postgresql-only-expressions)       |
| Temporal builders exported from `relq`, such as `extract` | [Temporal builders](#temporal-builders)                           |
| Row-locking clauses, such as `for_update()`               | [Row locking](#row-locking)                                       |
| Schema-qualified tables, `Table(name, schema=...)`        | [Schema](./schema.md#schema-qualified-tables)                     |
| Data-modifying CTEs, `with_modifying`                     | [Queries](./queries.md#data-modifying-ctes)                       |
| `ON CONFLICT` arbiter and action predicates               | [Data manipulation](./dml.md#conflict-predicates-postgresql-only) |

CTE materialization, `with_(..., materialized=True)`, compiles for both engines.
[Engine versions](./installation.md#engine-versions) lists the minimum server
versions.

## Separate import

The expressions in `relq.postgres` have their own import:

```python
from relq.postgres import cast_uuid, json_text, regex_match
```

## Temporal builders

These are exported from `relq` and compile for PostgreSQL only:

- `extract`, `date_trunc`, `date_bin`, `age`, and `at_time_zone`. `extract`
  takes an `ExtractField` and `date_trunc` takes a `TruncUnit`. A plain string
  raises `TypeError`.
- The clocks `transaction_timestamp`, `statement_timestamp`, `clock_timestamp`,
  `current_date`, `current_time`, `local_time`, and `local_timestamp`.
- The constructors `make_date`, `make_time`, `make_timestamp`,
  `make_timestamptz`, `make_interval`, and `to_timestamp`.
- Interval arithmetic: `add_interval`, `subtract_interval`, `negate_interval`,
  `multiply_interval`, `divide_interval`, and `justify_days`, `justify_hours`,
  and `justify_interval`.
- `date_difference`, `time_difference`, `timestamp_difference`, and `overlaps`.

With a `jobs` table that has a `created_at: Column[AwareDateTime]`:

```python
from relq import ExtractField, TruncUnit, date_trunc, extract, select

day = date_trunc(TruncUnit.DAY, jobs.created_at)
year = extract(ExtractField.YEAR, jobs.created_at)
query = select(day, year).from_(jobs)
```

## Row locking

`SelectQuery` has `for_update`, `for_no_key_update`, `for_share`, and
`for_key_share`. Each takes the tables to lock, or none to lock every table in
the query. `no_wait()` and `skip_locked()` follow a lock clause and set its wait
policy. A lock clause takes at most one wait policy.

```python
# jobs.state is a Column[str]
query = select(jobs.id).from_(jobs).where(jobs.state.eq("pending")).for_update().skip_locked()
```

A compound query arm cannot carry a locking clause. See
[Queries](./queries.md#set-operations).
