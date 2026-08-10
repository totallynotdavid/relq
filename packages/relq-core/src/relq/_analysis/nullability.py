"""Outer-join NULL-extension analysis."""

from relq._ast import SelectNode


def null_extended_sources(node: SelectNode) -> frozenset[str]:
    """Return local relation references an outer join can NULL-extend."""
    if node.from_source is None:
        return frozenset()
    visible = {node.from_source.reference}
    nullable: set[str] = set()
    for join in node.joins:
        match join.kind:
            case "left":
                nullable.add(join.source.reference)
            case "right":
                nullable.update(visible)
            case "full":
                nullable.update(visible)
                nullable.add(join.source.reference)
            case "inner" | "cross":
                pass
            case _:
                raise ValueError(f"unsupported join kind: {join.kind!r}")
        visible.add(join.source.reference)
    return frozenset(nullable)
