"""Split a large row stream into statements that fit a dialect's parameter ceiling."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from itertools import batched, chain


def batch_rows(
    rows: Iterable[Mapping[str, object]], *, max_parameters: int
) -> Iterator[tuple[Mapping[str, object], ...]]:
    """Yield the largest row batches whose bound values fit ``max_parameters``.

    Each cell counts as one parameter, which holds for plain values. A cell that
    is an expression with its own bound values can still push a full batch over
    the ceiling, and the compiler then rejects that statement.

    The first row fixes the width. Rows are read lazily, so a width mismatch is
    reported by ``values_many`` when the offending batch is built.
    """
    iterator = iter(rows)
    first = next(iterator, None)
    if first is None:
        return
    width = len(first)
    if width == 0:
        raise ValueError("a row must contain at least one column")
    size = max_parameters // width
    if size == 0:
        raise ValueError(
            f"a row of {width} columns exceeds the {max_parameters}-parameter statement ceiling"
        )
    yield from batched(chain((first,), iterator), size)
