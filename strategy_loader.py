from __future__ import annotations

from quant_platform_kit.common.strategies import (
    StrategyDefinition,
    load_strategy_entrypoint,
)
from quant_platform_kit.strategy_contracts import StrategyEntrypoint, StrategyRuntimeAdapter
from us_equity_strategies import get_platform_runtime_adapter

from strategy_registry import (
    FIRSTRADE_PLATFORM,
    get_strategy_adapter_source_platform,
    resolve_strategy_definition,
)


def load_strategy_definition(raw_profile: str | None) -> StrategyDefinition:
    return resolve_strategy_definition(
        raw_profile,
        platform_id=FIRSTRADE_PLATFORM,
    )


def load_strategy_entrypoint_for_profile(raw_profile: str | None) -> StrategyEntrypoint:
    definition = load_strategy_definition(raw_profile)
    runtime_adapter = load_strategy_runtime_adapter_for_profile(raw_profile)
    source_platform = get_strategy_adapter_source_platform()
    return load_strategy_entrypoint(
        definition,
        platform_id=source_platform,
        available_inputs=runtime_adapter.available_inputs,
        available_capabilities=runtime_adapter.available_capabilities,
    )


def load_strategy_runtime_adapter_for_profile(raw_profile: str | None) -> StrategyRuntimeAdapter:
    definition = load_strategy_definition(raw_profile)
    return get_platform_runtime_adapter(
        definition.profile,
        platform_id=get_strategy_adapter_source_platform(),
    )

