"""Local source-reference analysis for compiler visibility checks."""

from relq._analysis.walk import walk
from relq._ast import ColumnNode, Node


def referenced_sources(node: Node | None) -> set[str]:
    """Return local sources; nested SELECT scopes are deliberately excluded."""
    if node is None:
        return set()
    return {item.source for item in walk(node) if isinstance(item, ColumnNode)}
