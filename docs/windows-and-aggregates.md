# Windows and aggregates

Aggregates (`count`, `sum`, `avg`, `min`, `max`) become windowed with `.over()`.
Ranking and distribution functions (`row_number`, `rank`, `dense_rank`,
`percent_rank`, `cume_dist`) require it:

```python
ranked = (
    row_number()
    .over()
    .partition_by(users.manager_id)
    .order_by(users.id.asc())
)
```

The resulting value is refined with `.partition_by(...)` and `.order_by(...)`
before selection. `Order.nulls_first()` / `.nulls_last()` make NULL placement
explicit, since SQLite and PostgreSQL default it differently.

Frames are explicit values, not SQL strings: `.rows_between(...)`,
`.range_between(...)`, and `.groups_between(...)` accept
`unbounded_preceding()`, `preceding(n)`, `current_row()`, `following(n)`, and
`unbounded_following()` as boundaries.

Aggregate `FILTER (WHERE ...)` is supported through `.filter(predicate)`. Named
windows and raw SQL fragments are not. Every window is declared inline where
it's used.
