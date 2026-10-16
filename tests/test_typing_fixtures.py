"""Focused static-contract tests, kept outside the normal type-check input."""

import inspect
import json
import typing
from collections.abc import Callable
from pathlib import Path
from subprocess import run
from typing import cast

import pytest
from relq.dml import DeleteQuery, InsertQuery, ModifyingCteBody, UpdateQuery
from relq.query import ModifyingQuery, SelectQuery

ROOT = Path(__file__).parents[1]


def _project(fixture: str) -> Path:
    return ROOT / "tests" / "typing" / f"{fixture}.json"


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


def test_the_modifying_cte_alias_resolves_at_runtime() -> None:
    # ModifyingCteBody names the DML builders, which import the SELECT builder, so a
    # TYPE_CHECKING-only definition would leave its value unevaluatable.
    value = cast(object, ModifyingCteBody.__value__)
    arms = cast(tuple[object, ...], typing.get_args(value))

    assert {typing.get_origin(arm) or arm for arm in arms} == {
        InsertQuery,
        UpdateQuery,
        DeleteQuery,
    }


# with_ and from_select name each other's builder modules, so a TYPE_CHECKING import on
# either side would leave the signature raising NameError.
def test_public_builder_signatures_resolve_at_runtime() -> None:
    builders: list[tuple[type, str]] = [
        (SelectQuery, "with_"),
        (SelectQuery, "with_recursive"),
        (SelectQuery, "with_modifying"),
        (ModifyingQuery, "with_modifying"),
        (InsertQuery, "from_select"),
        (UpdateQuery, "returning"),
        (DeleteQuery, "returning"),
    ]
    for owner, name in builders:
        function = cast(Callable[..., object], inspect.getattr_static(owner, name))
        # A method's annotations may name its class's own type parameters.
        class_parameters = {parameter.__name__: parameter for parameter in owner.__type_params__}
        assert cast(dict[str, object], typing.get_type_hints(function, localns=class_parameters))
        assert inspect.signature(function, eval_str=True, locals=class_parameters).parameters


@pytest.mark.parametrize(
    ("fixture", "rules"),
    [
        ("bad_window", {"reportArgumentType"}),
        ("bad_ast_boundary", {"reportAttributeAccessIssue", "reportArgumentType"}),
        ("bad_compounds", {"reportArgumentType"}),
        ("bad_conditional", {"reportArgumentType"}),
        ("bad_temporal", {"reportArgumentType"}),
        ("bad_dml_ctes", {"reportArgumentType"}),
        ("bad_execution", {"reportCallIssue"}),
    ],
)
def test_a_bad_typing_fixture_fails_for_the_intended_contracts(
    fixture: str, rules: set[str]
) -> None:
    returncode, diagnostics = _basedpyright(_project(fixture))
    assert returncode != 0
    assert rules <= {diagnostic.get("rule") for diagnostic in diagnostics}


def test_a_returning_statement_is_not_streamable() -> None:
    returncode, diagnostics = _basedpyright(_project("bad_execution"))
    streaming = [
        diagnostic for diagnostic in diagnostics if "@fetch_iter" in str(diagnostic["message"])
    ]
    assert returncode != 0
    assert [diagnostic.get("rule") for diagnostic in streaming] == ["reportArgumentType"]
