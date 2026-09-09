from dataclasses import dataclass

from relq import count, cte, delete_from, select, select_model, update

from .good import DocumentIds, documents

removed = cte(DocumentIds, "removed")

# A data-modifying CTE must publish rows: no RETURNING, no output relation.
select(count()).from_(removed).with_(
    removed, delete_from(documents).where(documents.owner.eq("ada"))
)

# ... and it must be bounded before it can define one.
select(count()).from_(removed).with_(removed, delete_from(documents).returning(documents.id))
select(count()).from_(removed).with_(
    removed, update(documents).values(owner="ada").returning(documents.id)
)


# A CTE body cannot declare a row model: the outer query owns the result shape.
@dataclass(frozen=True)
class DocumentRow:
    id: int


select(count()).from_(removed).with_(
    removed,
    delete_from(documents)
    .where(documents.owner.eq("ada"))
    .returning_model(DocumentRow, documents.id),
)
select(count()).from_(removed).with_(
    removed, select_model(DocumentRow, documents.id).from_(documents)
)
select(removed.id).from_(removed).with_recursive(
    removed, select_model(DocumentRow, documents.id).from_(documents)
)
