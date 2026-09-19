# Windows and aggregates

Aggregates (`count`, `sum`, `avg`, `min`, `max`) become windowed with `.over()`.
Ranking and distribution functions (`row_number`, `rank`, `dense_rank`,
`percent_rank`, `cume_dist`) require it:

```python
ranked = row_number().over().partition_by(users.manager_id).order_by(users.id.asc())
```

Refine the windowed value with `.partition_by(...)` and `.order_by(...)` before
selecting it. `Order.nulls_first()` and `.nulls_last()` set NULL placement
explicitly, because SQLite and PostgreSQL default it differently.

Frames are values, not SQL strings. `.rows_between(...)`, `.range_between(...)`,
and `.groups_between(...)` take `unbounded_preceding()`, `preceding(n)`,
`current_row()`, `following(n)`, and `unbounded_following()` as boundaries.

`.filter(predicate)` adds an aggregate `FILTER (WHERE ...)`. Named windows and
raw SQL fragments are not supported. Each window is declared inline where it is
used.
