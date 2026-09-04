"""Focused static-contract tests, kept outside the normal type-check input."""

import json
from pathlib import Path
from subprocess import run
from typing import cast

ROOT = Path(__file__).parents[1]
GOOD = ROOT / "tests" / "typing" / "good.json"
BAD = ROOT / "tests" / "typing" / "bad_window.json"
BAD_AST_BOUNDARY = ROOT / "tests" / "typing" / "bad_ast_boundary.json"
BAD_COMPOUNDS = ROOT / "tests" / "typing" / "bad_compounds.json"
BAD_CONDITIONAL = ROOT / "tests" / "typing" / "bad_conditional.json"
BAD_CONFLICT_TARGET = ROOT / "tests" / "typing" / "bad_conflict_target.json"
BAD_DIALECT = ROOT / "tests" / "typing" / "bad_dialect.json"
BAD_EXECUTION = ROOT / "tests" / "typing" / "bad_execution.json"
BAD_TEMPORAL = ROOT / "tests" / "typing" / "bad_temporal.json"
GENERATED = ROOT / "tests" / "typing" / "generated_batch.json"
GENERATED_SCHEMA = ROOT / "tests" / "typing" / "generated_schema.py"
SNAPSHOT = ROOT / "tests" / "snapshots" / "sqlite_schema.py"


def _basedpyright(project: Path) -> tuple[int, list[dict[str, object]]]:
    result = run(
        ("basedpyright", "--outputjson", "--project", str(project)),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    payload = _json_object(result.stdout)
    diagnostics = payload.get("generalDiagnostics")
    if not isinstance(diagnostics, list):
        raise TypeError("basedpyright did not produce diagnostics")
    normalized = cast(list[object], diagnostics)
    typed: list[dict[str, object]] = []
    for item in normalized:
        if not isinstance(item, dict):
            raise TypeError("basedpyright did not produce diagnostic objects")
        typed.append(cast(dict[str, object], item))
    return result.returncode, typed


def _json_object(text: str) -> dict[str, object]:
    payload = cast(object, json.loads(text))
    if not isinstance(payload, dict):
        raise TypeError("basedpyright did not produce an object result")
    return cast(dict[str, object], payload)


def test_good_typing_fixture_is_clean() -> None:
    returncode, diagnostics = _basedpyright(GOOD)
    assert returncode == 0, diagnostics


def test_bad_typing_fixture_fails_for_the_intended_contracts() -> None:
    returncode, diagnostics = _basedpyright(BAD)
    rules = {diagnostic.get("rule") for diagnostic in diagnostics}
    assert returncode != 0
    assert "reportArgumentType" in rules


def test_ast_boundary_fixture_rejects_public_node_access_and_structural_fakes() -> None:
    returncode, diagnostics = _basedpyright(BAD_AST_BOUNDARY)
    rules = {diagnostic.get("rule") for diagnostic in diagnostics}
    assert returncode != 0
    assert "reportAttributeAccessIssue" in rules
    assert "reportArgumentType" in rules


def test_bad_compound_fixture_fails_for_the_result_shape_contract() -> None:
    returncode, diagnostics = _basedpyright(BAD_COMPOUNDS)
    rules = {diagnostic.get("rule") for diagnostic in diagnostics}
    assert returncode != 0
    assert "reportArgumentType" in rules


def test_bad_conditional_fixture_fails_for_closed_case_contracts() -> None:
    returncode, diagnostics = _basedpyright(BAD_CONDITIONAL)
    rules = {diagnostic.get("rule") for diagnostic in diagnostics}
    assert returncode != 0
    assert "reportArgumentType" in rules


def test_conflict_targets_must_still_be_columns() -> None:
    returncode, diagnostics = _basedpyright(BAD_CONFLICT_TARGET)
    rules = {diagnostic.get("rule") for diagnostic in diagnostics}
    assert returncode != 0
    # Non-columns and structural lookalikes are rejected by the parameter type;
    # the base and its subclasses cannot be constructed without relq's private
    # token, so inheriting it is not a way around that either.
    assert "reportArgumentType" in rules
    assert "reportCallIssue" in rules


def test_bad_temporal_fixture_fails_for_temporal_domains() -> None:
    returncode, diagnostics = _basedpyright(BAD_TEMPORAL)
    rules = {diagnostic.get("rule") for diagnostic in diagnostics}
    assert returncode != 0
    assert "reportArgumentType" in rules


def test_dialect_legality_is_a_compiler_contract() -> None:
    returncode, diagnostics = _basedpyright(BAD_DIALECT)
    assert returncode == 0
    assert diagnostics == []


def test_non_row_dml_is_not_fetchable() -> None:
    returncode, diagnostics = _basedpyright(BAD_EXECUTION)
    rules = {diagnostic.get("rule") for diagnostic in diagnostics}
    assert returncode != 0
    assert "reportCallIssue" in rules


def test_generated_batch_helper_type_checks() -> None:
    assert GENERATED_SCHEMA.read_text() == SNAPSHOT.read_text()
    returncode, diagnostics = _basedpyright(GENERATED)
    assert returncode == 0, diagnostics
