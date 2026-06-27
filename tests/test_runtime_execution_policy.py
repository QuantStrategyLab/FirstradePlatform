from __future__ import annotations

import importlib
import sys
import types

import pytest

from quant_platform_kit.common.execution_capabilities import (
    FRACTIONAL_SHARE_EXECUTION_CAPABILITY,
    FRACTIONAL_SHARE_EXECUTION_SKIP_REASON,
    fractional_share_execution_unsupported_reason,
)
from quant_platform_kit.common.strategies import (
    PlatformCapabilityMatrix,
    StrategyCatalog,
    StrategyDefinition,
    US_EQUITY_DOMAIN,
)

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
QPK_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(QPK_SRC) not in sys.path:
    sys.path.insert(0, str(QPK_SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_DCA_DEFINITIONS = {
    "nasdaq_sp500_smart_dca": StrategyDefinition(
        profile="nasdaq_sp500_smart_dca",
        domain="us_equity",
        supported_platforms=frozenset({"firstrade"}),
        compatible_capabilities=frozenset({FRACTIONAL_SHARE_EXECUTION_CAPABILITY}),
    ),
    "ibit_smart_dca": StrategyDefinition(
        profile="ibit_smart_dca",
        domain="us_equity",
        supported_platforms=frozenset({"firstrade"}),
        compatible_capabilities=frozenset({FRACTIONAL_SHARE_EXECUTION_CAPABILITY}),
    ),
    "tqqq_growth_income": StrategyDefinition(
        profile="tqqq_growth_income",
        domain="us_equity",
        supported_platforms=frozenset({"firstrade"}),
        compatible_capabilities=frozenset(),
    ),
    "global_etf_rotation": StrategyDefinition(
        profile="global_etf_rotation",
        domain="us_equity",
        supported_platforms=frozenset({"firstrade"}),
        compatible_capabilities=frozenset(),
    ),
}
_FIRSTRADE_CAPABILITY_MATRIX = PlatformCapabilityMatrix(
    platform_id="firstrade",
    supported_domains=frozenset({US_EQUITY_DOMAIN}),
    supported_target_modes=frozenset({"weight", "value"}),
    supported_inputs=frozenset(
        {
            "benchmark_history",
            "market_history",
            "portfolio_snapshot",
            "derived_indicators",
            "feature_snapshot",
            "indicators",
            "account_state",
            "snapshot",
        }
    ),
    supported_capabilities=frozenset({FRACTIONAL_SHARE_EXECUTION_CAPABILITY}),
)
_FAKE_CATALOG = StrategyCatalog(definitions=_DCA_DEFINITIONS)

_fake_registry = types.ModuleType("strategy_registry")
_fake_registry.PLATFORM_CAPABILITY_MATRIX = _FIRSTRADE_CAPABILITY_MATRIX
_fake_registry.STRATEGY_CATALOG = _FAKE_CATALOG


@pytest.fixture
def notional_buy_fn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "strategy_registry", _fake_registry)
    rep = importlib.import_module("runtime_execution_policy")
    rep = importlib.reload(rep)
    return rep.notional_buy_execution_enabled


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("nasdaq_sp500_smart_dca", None),
        ("ibit_smart_dca", None),
        ("tqqq_growth_income", None),
        ("global_etf_rotation", None),
    ],
)
def test_fractional_share_execution_unsupported_reason_without_capability(profile: str, expected: str | None) -> None:
    matrix_without_fractional = PlatformCapabilityMatrix(
        platform_id="firstrade",
        supported_domains=frozenset({US_EQUITY_DOMAIN}),
        supported_target_modes=frozenset({"weight", "value"}),
        supported_inputs=frozenset(),
        supported_capabilities=frozenset(),
    )
    dca_expected = (
        FRACTIONAL_SHARE_EXECUTION_SKIP_REASON
        if profile in {"nasdaq_sp500_smart_dca", "ibit_smart_dca"}
        else None
    )
    assert (
        fractional_share_execution_unsupported_reason(
            profile,
            strategy_catalog=_FAKE_CATALOG,
            capability_matrix=matrix_without_fractional,
        )
        == dca_expected
    )


def test_dca_profiles_require_fractional_share_capability() -> None:
    for profile in ("nasdaq_sp500_smart_dca", "ibit_smart_dca"):
        definition = _FAKE_CATALOG.definitions[profile]
        assert FRACTIONAL_SHARE_EXECUTION_CAPABILITY in definition.compatible_capabilities


def test_firstrade_capability_matrix_allows_dca_profiles(notional_buy_fn) -> None:
    for profile in ("nasdaq_sp500_smart_dca", "ibit_smart_dca"):
        assert (
            fractional_share_execution_unsupported_reason(
                profile,
                strategy_catalog=_FAKE_CATALOG,
                capability_matrix=_FIRSTRADE_CAPABILITY_MATRIX,
            )
            is None
        )
        assert notional_buy_fn(profile) is True


def test_notional_buy_execution_disabled_for_non_dca_profiles(notional_buy_fn) -> None:
    assert notional_buy_fn("global_etf_rotation") is False
    assert notional_buy_fn("tqqq_growth_income") is False
