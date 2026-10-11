"""B12 parity: semantics/display facade path == legacy mixed-annotations output."""

from __future__ import annotations

import json

import pytest

import decision_mapper
from value_target_parity_cases import GOLDEN_PATH, build_cases, canonical

_GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
_CASES = build_cases()


def test_golden_covers_every_case() -> None:
    assert sorted(_GOLDEN) == sorted(name for name, _ in _CASES)


@pytest.mark.parametrize("name,kwargs", _CASES, ids=[name for name, _ in _CASES])
def test_plan_matches_legacy_golden(name: str, kwargs: dict) -> None:
    plan = decision_mapper.map_strategy_decision_to_plan(**kwargs)
    assert canonical(plan) == _GOLDEN[name]


@pytest.mark.parametrize("name,kwargs", _CASES, ids=[name for name, _ in _CASES])
def test_plan_matches_qpk_compat_facade(name: str, kwargs: dict, monkeypatch) -> None:
    """Route the same inputs through the retained QPK compat facade and compare."""
    from quant_platform_kit.common.execution_translation import build_value_target_runtime_plan
    from quant_platform_kit.common.presentation import merge_value_target_execution_annotations

    modern = decision_mapper.map_strategy_decision_to_plan(**kwargs)

    def _legacy(decision, *, strategy_profile, portfolio_inputs, semantics, display, **rest):
        return build_value_target_runtime_plan(
            decision,
            strategy_profile=strategy_profile,
            portfolio_inputs=portfolio_inputs,
            annotations=merge_value_target_execution_annotations(semantics, display),
            **rest,
        )

    monkeypatch.setattr(decision_mapper, "build_value_target_execution_runtime_plan", _legacy)
    legacy = decision_mapper.map_strategy_decision_to_plan(**kwargs)
    assert canonical(modern) == canonical(legacy)


def test_production_mapper_no_longer_uses_legacy_value_target_api() -> None:
    source = open(decision_mapper.__file__, encoding="utf-8").read()
    assert "build_value_target_runtime_plan" not in source
    assert "ValueTargetExecutionAnnotations" not in source
    assert "build_value_target_execution_annotations" not in source
