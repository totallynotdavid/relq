"""Focused static-contract tests, kept outside the normal type-check input."""

import inspect
import json
import typing
from collections.abc import Callable
from pathlib import Path
from subprocess import run
from typing import cast

from relq.dml import CteQuery, DeleteQuery, InsertQuery, UpdateQuery
from relq.query import SelectQuery

ROOT = Path(__file__).parents[1]
GOOD = ROOT / "tests" / "typing" / "good.json"
BAD = ROOT / "tests" / "typing" / "bad_window.json"
BAD_COMPOUNDS = ROOT / "tests" / "typing" / "bad_compounds.json"
BAD_CONDITIONAL = ROOT / "tests" / "typing" / "bad_conditional.json"
BAD_DML_CTES = ROOT / "tests" / "typing" / "bad_dml_ctes.json"
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


def test_the_cte_query_alias_resolves_at_runtime() -> None:
    """A type alias in a public signature must be introspectable, not a NameError.

    ``CteQuery`` names the DML builders, which import the SELECT builder, so a
    ``TYPE_CHECKING``-only definition would leave its value unevaluatable.
    """
    value = cast(object, CteQuery.__value__)
    arms = cast(tuple[object, ...], typing.get_args(value))

    assert {typing.get_origin(arm) or arm for arm in arms} == {
        SelectQuery,
        InsertQuery,
        UpdateQuery,
        DeleteQuery,
    }


def _assert_signature_resolves(owner: type, name: str) -> None:
    function = cast(Callable[..., object], inspect.getattr_static(owner, name))
    assert cast(dict[str, object], typing.get_type_hints(function))
    assert inspect.signature(function, eval_str=True).parameters


def test_public_builder_signatures_resolve_at_runtime() -> None:
    """Annotations must be introspectable, not TYPE_CHECKING-only forward references.

    ``with_`` and ``from_select`` name each other's builder modules, so a
    ``TYPE_CHECKING`` import on either side leaves that signature raising
    ``NameError`` for ``get_type_hints`` and ``inspect.signature``.
    """
    _assert_signature_resolves(SelectQuery, "with_")
    _assert_signature_resolves(SelectQuery, "with_recursive")
    _assert_signature_resolves(InsertQuery, "from_select")
    _assert_signature_resolves(UpdateQuery, "returning")
    _assert_signature_resolves(DeleteQuery, "returning")


def test_good_typing_fixture_is_clean() -> None:
    returncode, diagnostics = _basedpyright(GOOD)
    assert returncode == 0, diagnostics


def test_bad_typing_fixture_fails_for_the_intended_contracts() -> None:
    returncode, diagnostics = _basedpyright(BAD)
    rules = {diagnostic.get("rule") for diagnostic in diagnostics}
    assert returncode != 0
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


def test_bad_dml_cte_fixture_fails_for_the_bounded_returning_contract() -> None:
    returncode, diagnostics = _basedpyright(BAD_DML_CTES)
    rules = {diagnostic.get("rule") for diagnostic in diagnostics}
    assert returncode != 0
    assert "reportArgumentType" in rules


def test_generated_batch_helper_type_checks() -> None:
    assert GENERATED_SCHEMA.read_text() == SNAPSHOT.read_text()
    returncode, diagnostics = _basedpyright(GENERATED)
    assert returncode == 0, diagnostics
