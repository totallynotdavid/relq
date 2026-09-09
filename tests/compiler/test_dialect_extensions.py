"""Contracts for the dialect-varying surface: schemas, CTE bodies, and PostgreSQL expressions."""

import uuid
from dataclasses import dataclass

import pytest
from relq import (
    Column,
    CteTable,
    DerivedTable,
    JsonValue,
    Table,
    column,
    count,
    cte,
    delete_from,
    insert_into,
    json_column,
    output_column,
    scalar,
    select,
    select_model,
    update,
)
from relq._compiler import compile_postgres, compile_sqlite
from relq.postgres import cast_uuid, json_text, regex_match


class Documents(Table):
    id: Column[uuid.UUID] = column(uuid.UUID)
    owner: Column[str] = column(str)
    payload: Column[JsonValue] = json_column()


class DocumentIds(CteTable):
    id: Column[uuid.UUID] = output_column(uuid.UUID)


class OwnerRefs(CteTable):
    owner: Column[str] = output_column(str)


class Archive(Table):
    id: Column[uuid.UUID] = column(uuid.UUID)


class DocumentTotals(DerivedTable):
    id: Column[uuid.UUID] = output_column(uuid.UUID)


@dataclass(frozen=True)
class DocumentRow:
    id: uuid.UUID


documents = Documents("documents")
archive = Archive("archive")
archived = Documents("documents", schema="archive")


def test_a_schema_is_two_identifiers_for_postgresql_and_rejected_for_sqlite() -> None:
    query = select(archived.id).from_(archived)

    assert compile_postgres(query).sql == 'select "documents"."id" from "archive"."documents"'
    with pytest.raises(ValueError, match="sqlite does not support schema-qualified tables"):
        compile_sqlite(query)


def test_a_schema_qualified_table_keeps_its_bare_correlation_name() -> None:
    """The schema names the relation, not the alias columns are qualified by."""
    query = (
        select(archived.id)
        .from_(archived)
        .inner_join(documents.as_("live"), on=documents.as_("live").id.eq(archived.id))
    )

    assert compile_postgres(query).sql == (
        'select "documents"."id" from "archive"."documents" '
        'inner join "documents" as "live" on ("live"."id" = "documents"."id")'
    )


def test_a_schema_qualified_table_is_the_dml_target_too() -> None:
    query = delete_from(archived).where(archived.owner.eq("ada"))

    assert compile_postgres(query).sql == (
        'delete from "archive"."documents" where ("documents"."owner" = $1)'
    )


def test_an_empty_schema_is_rejected_at_declaration() -> None:
    with pytest.raises(ValueError, match="table schema must not be empty"):
        Documents("documents", schema="")


def test_materialized_ctes_render_for_both_dialects() -> None:
    """SQLite gained MATERIALIZED in the same release as RETURNING, which relq already requires."""
    ids = cte(DocumentIds, "ids")
    query = (
        select(ids.id)
        .from_(ids)
        .with_(ids, select(documents.id).from_(documents), materialized=True)
    )

    assert compile_sqlite(query).sql == (
        'with "ids" as materialized (select "documents"."id" from "documents") '
        'select "ids"."id" from "ids"'
    )
    assert compile_postgres(query).sql == compile_sqlite(query).sql


def test_an_unmaterialized_cte_still_leaves_the_choice_to_the_planner() -> None:
    ids = cte(DocumentIds, "ids")
    query = select(ids.id).from_(ids).with_(ids, select(documents.id).from_(documents))

    assert " as (select " in compile_postgres(query).sql


def test_a_delete_cte_publishes_its_returning_rows_to_the_outer_query() -> None:
    removed = cte(DocumentIds, "removed")
    deleted = delete_from(documents).where(documents.owner.eq("ada")).returning(documents.id)
    query = select(count()).from_(removed).with_(removed, deleted)

    assert compile_postgres(query).sql == (
        'with "removed" as (delete from "documents" where ("documents"."owner" = $1) '
        'returning "documents"."id") select count(*) from "removed"'
    )
    with pytest.raises(ValueError, match="sqlite does not support data-modifying"):
        compile_sqlite(query)


def test_insert_and_update_ctes_publish_their_returning_rows_too() -> None:
    inserted = cte(DocumentIds, "inserted")
    touched = cte(OwnerRefs, "touched")
    query = (
        select(count())
        .from_(inserted)
        .with_(
            inserted,
            insert_into(documents).values(owner="ada").returning(documents.id),
        )
        .with_(
            touched,
            update(documents)
            .values(owner="ada")
            .where(documents.id.eq(1))
            .returning(documents.owner),
        )
    )

    compiled = compile_postgres(query)
    assert 'with "inserted" as (insert into "documents" ("owner") values ($1)' in compiled.sql
    assert '"touched" as (update "documents" set "owner" = $2' in compiled.sql


def test_a_data_modifying_cte_must_declare_its_output_relation() -> None:
    removed = cte(DocumentIds, "removed")
    without_returning = delete_from(documents).where(documents.owner.eq("ada"))

    with pytest.raises(ValueError, match="data-modifying CTE requires returning"):
        select(count()).from_(removed).with_(removed, without_returning)  # pyright: ignore[reportArgumentType]


def test_a_data_modifying_cte_output_schema_must_match_its_relation() -> None:
    owners = cte(OwnerRefs, "owners")
    deleted = delete_from(documents).where(documents.owner.eq("ada")).returning(documents.id)

    with pytest.raises(ValueError, match="output schema does not match"):
        select(count()).from_(owners).with_(owners, deleted)


def test_a_later_data_modifying_cte_reads_an_earlier_one() -> None:
    """A CTE body sees the WITH chain bound before it, whether it is a SELECT or DML."""
    removed = cte(DocumentIds, "removed")
    copied = cte(DocumentIds, "copied")
    query = (
        select(count())
        .from_(copied)
        .with_(
            removed,
            delete_from(documents).where(documents.owner.eq("ada")).returning(documents.id),
        )
        .with_(
            copied,
            insert_into(archive)
            .from_select(select(removed.id).from_(removed), archive.id)
            .returning(archive.id),
        )
    )

    assert compile_postgres(query).sql == (
        'with "removed" as (delete from "documents" where ("documents"."owner" = $1) '
        'returning "documents"."id"), '
        '"copied" as (insert into "archive" ("id") select "removed"."id" from "removed" '
        'returning "archive"."id") select count(*) from "copied"'
    )


def test_a_data_modifying_cte_still_cannot_read_a_later_one() -> None:
    removed = cte(DocumentIds, "removed")
    copied = cte(DocumentIds, "copied")
    query = (
        select(count())
        .from_(copied)
        .with_(
            copied,
            insert_into(archive)
            .from_select(select(removed.id).from_(removed), archive.id)
            .returning(archive.id),
        )
        .with_(
            removed,
            delete_from(documents).where(documents.owner.eq("ada")).returning(documents.id),
        )
    )

    with pytest.raises(ValueError, match="references CTE 'removed' before it is declared"):
        compile_postgres(query)


def test_a_forward_reference_is_caught_through_a_nested_subquery() -> None:
    """WITH binds in declaration order at every depth, not just in a body's own FROM."""
    early = cte(DocumentIds, "early")
    later = cte(DocumentIds, "later")
    reaches_later = documents.id.in_(select(later.id).from_(later))

    through_dml = (
        select(count())
        .from_(early)
        .with_(early, delete_from(documents).where(reaches_later).returning(documents.id))
        .with_(later, select(documents.id).from_(documents))
    )
    through_select = (
        select(count())
        .from_(early)
        .with_(early, select(documents.id).from_(documents).where(reaches_later))
        .with_(later, select(documents.id).from_(documents))
    )
    derived = select(later.id).from_(later).as_(DocumentTotals, "totals")
    through_derived = (
        select(count())
        .from_(early)
        .with_(early, select(derived.id).from_(derived))
        .with_(later, select(documents.id).from_(documents))
    )

    for query in (through_dml, through_select, through_derived):
        with pytest.raises(ValueError, match="references CTE 'later' before it is declared"):
            compile_postgres(query)


def test_a_nested_query_reads_the_ctes_it_declares_itself() -> None:
    """The visibility check must count a nested WITH, not just inherited names."""
    inner = cte(DocumentIds, "inner_ids")
    outer = cte(DocumentIds, "outer_ids")
    body = select(inner.id).from_(inner).with_(inner, select(documents.id).from_(documents))
    query = select(count()).from_(outer).with_(outer, body)

    assert compile_postgres(query).sql == (
        'with "outer_ids" as ('
        'with "inner_ids" as (select "documents"."id" from "documents") '
        'select "inner_ids"."id" from "inner_ids"'
        ') select count(*) from "outer_ids"'
    )


def test_a_subquery_still_reads_a_cte_declared_before_it() -> None:
    early = cte(DocumentIds, "early")
    later = cte(DocumentIds, "later")
    query = (
        select(count())
        .from_(later)
        .with_(early, select(documents.id).from_(documents))
        .with_(
            later,
            select(documents.id)
            .from_(documents)
            .where(documents.id.in_(select(early.id).from_(early))),
        )
    )

    assert 'in (select "early"."id" from "early")' in compile_postgres(query).sql


def test_a_cte_source_cannot_declare_a_row_model() -> None:
    """A CTE's rows never reach the executor, so an adapter here could decode nothing."""
    removed = cte(DocumentIds, "removed")
    modelled_delete = (
        delete_from(documents)
        .where(documents.owner.eq("ada"))
        .returning_model(DocumentRow, documents.id)
    )
    modelled_select = select_model(DocumentRow, documents.id).from_(documents)

    with pytest.raises(ValueError, match="cannot declare a row model"):
        select(count()).from_(removed).with_(removed, modelled_delete)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="cannot declare a row model"):
        select(count()).from_(removed).with_(removed, modelled_select)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="cannot declare a row model"):
        select(removed.id).from_(removed).with_recursive(removed, modelled_select)  # pyright: ignore[reportArgumentType]


def test_a_data_modifying_cte_cannot_hide_inside_a_nested_scope() -> None:
    """A data-modifying CTE runs once per statement, so only the outermost WITH accepts one."""
    removed = cte(DocumentIds, "removed")
    deleted = delete_from(documents).where(documents.owner.eq("ada")).returning(documents.id)
    hidden = select(count()).from_(removed).with_(removed, deleted)
    outer = select(documents.id).from_(documents).where(documents.id.eq(scalar(hidden)))

    with pytest.raises(ValueError, match="must be declared on the outermost query"):
        compile_postgres(outer)


def test_postgres_only_expressions_compile_for_postgresql_and_are_rejected_elsewhere() -> None:
    query = (
        select(cast_uuid(json_text(documents.payload, "job_id")).as_("job_id"))
        .from_(documents)
        .where(regex_match(json_text(documents.payload, "job_id"), "^[0-9a-f-]+$"))
    )

    compiled = compile_postgres(query)
    assert compiled.sql == (
        'select cast(("documents"."payload" ->> $1) as uuid) as "job_id" from "documents" '
        'where (("documents"."payload" ->> $2) ~ $3)'
    )
    assert compiled.parameters == ("job_id", "job_id", "^[0-9a-f-]+$")
    with pytest.raises(ValueError, match="PostgreSQL-only cast_uuid"):
        compile_sqlite(query)


def test_regex_match_selects_its_case_sensitivity_operator() -> None:
    insensitive = (
        select(documents.id)
        .from_(documents)
        .where(regex_match(documents.owner, "^ada$", insensitive=True))
    )

    assert '("documents"."owner" ~* $1)' in compile_postgres(insensitive).sql


def test_regex_match_accepts_another_column_as_its_pattern() -> None:
    query = (
        select(documents.id).from_(documents).where(regex_match(documents.owner, documents.owner))
    )

    assert '("documents"."owner" ~ "documents"."owner")' in compile_postgres(query).sql


def test_json_text_requires_a_member_key() -> None:
    with pytest.raises(ValueError, match="non-empty member key"):
        json_text(documents.payload, "")


def test_each_postgres_only_expression_names_itself_when_sqlite_rejects_it() -> None:
    extraction = select(json_text(documents.payload, "job_id").as_("job")).from_(documents)
    matching = select(documents.id).from_(documents).where(regex_match(documents.owner, "^a"))

    with pytest.raises(ValueError, match="PostgreSQL-only json_text"):
        compile_sqlite(extraction)
    with pytest.raises(ValueError, match="PostgreSQL-only regex_match"):
        compile_sqlite(matching)


def test_json_text_needs_no_outer_join_marker_for_its_already_optional_result() -> None:
    """json_text returns Expr[str | None], so an outer join widens nothing."""
    query = (
        select(archive.id, json_text(documents.payload, "city").as_("city"))
        .from_(archive)
        .left_join(documents, on=documents.id.eq(archive.id))
    )

    assert '("documents"."payload" ->> $1) as "city"' in compile_postgres(query).sql


def test_cast_uuid_still_needs_the_marker_because_it_keeps_its_operand_type() -> None:
    """cast_uuid(Expr[str]) is Expr[UUID], so an outer join does widen it."""
    query = (
        select(archive.id, cast_uuid(documents.owner).as_("owner_id"))
        .from_(archive)
        .left_join(documents, on=documents.id.eq(archive.id))
    )

    with pytest.raises(ValueError, match="outer join can NULL-extend"):
        compile_postgres(query)

    acknowledged = (
        select(archive.id, cast_uuid(documents.owner).as_("owner_id").nullable())
        .from_(archive)
        .left_join(documents, on=documents.id.eq(archive.id))
    )
    assert 'cast("documents"."owner" as uuid) as "owner_id"' in compile_postgres(acknowledged).sql
