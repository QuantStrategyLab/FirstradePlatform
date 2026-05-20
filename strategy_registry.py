from __future__ import annotations

import os

from quant_platform_kit.common.strategies import (
    PlatformStrategyPolicy,
    StrategyDefinition,
    StrategyMetadata,
    US_EQUITY_DOMAIN,
    build_platform_profile_matrix,
    build_platform_profile_status_matrix,
    get_catalog_strategy_metadata,
    resolve_platform_strategy_definition,
)
from us_equity_strategies import (
    get_runtime_enabled_profiles,
    get_strategy_catalog,
)

FIRSTRADE_PLATFORM = "firstrade"

# Firstrade is not first-class in UsEquityStrategies yet. Until that repo adds
# a native adapter key, this platform uses the same value-native strategy input
# shape as LongBridge/Schwab while reporting runtime identity as Firstrade.
DEFAULT_STRATEGY_ADAPTER_SOURCE_PLATFORM = "longbridge"
SUPPORTED_STRATEGY_ADAPTER_SOURCE_PLATFORMS = frozenset({"longbridge", "schwab"})

PLATFORM_SUPPORTED_DOMAINS: dict[str, frozenset[str]] = {
    FIRSTRADE_PLATFORM: frozenset({US_EQUITY_DOMAIN}),
}

STRATEGY_CATALOG = get_strategy_catalog()
FIRSTRADE_ROLLOUT_ALLOWLIST = get_runtime_enabled_profiles()
FIRSTRADE_ENABLED_PROFILES = frozenset(sorted(FIRSTRADE_ROLLOUT_ALLOWLIST))
PLATFORM_POLICY = PlatformStrategyPolicy(
    platform_id=FIRSTRADE_PLATFORM,
    supported_domains=PLATFORM_SUPPORTED_DOMAINS[FIRSTRADE_PLATFORM],
    enabled_profiles=FIRSTRADE_ENABLED_PROFILES,
    default_profile="",
    rollback_profile="",
    require_explicit_profile=True,
)

SUPPORTED_STRATEGY_PROFILES = FIRSTRADE_ENABLED_PROFILES
_SELECTION_ROLE_FIELDS = frozenset({"is_default", "is_rollback"})


def _without_selection_role_fields(row: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in row.items() if key not in _SELECTION_ROLE_FIELDS}


def get_strategy_adapter_source_platform() -> str:
    value = os.getenv(
        "FIRSTRADE_STRATEGY_ADAPTER_SOURCE_PLATFORM",
        DEFAULT_STRATEGY_ADAPTER_SOURCE_PLATFORM,
    )
    normalized = str(value or "").strip().lower()
    if normalized not in SUPPORTED_STRATEGY_ADAPTER_SOURCE_PLATFORMS:
        supported = ", ".join(sorted(SUPPORTED_STRATEGY_ADAPTER_SOURCE_PLATFORMS))
        raise ValueError(
            "FIRSTRADE_STRATEGY_ADAPTER_SOURCE_PLATFORM must be one of: "
            f"{supported}"
        )
    return normalized


def get_eligible_profiles_for_platform(platform_id: str) -> frozenset[str]:
    if platform_id != FIRSTRADE_PLATFORM:
        return frozenset()
    return FIRSTRADE_ENABLED_PROFILES


def get_supported_profiles_for_platform(platform_id: str) -> frozenset[str]:
    if platform_id != FIRSTRADE_PLATFORM:
        return frozenset()
    return FIRSTRADE_ENABLED_PROFILES


def get_platform_profile_matrix() -> list[dict[str, object]]:
    return [
        _without_selection_role_fields(row)
        for row in build_platform_profile_matrix(STRATEGY_CATALOG, policy=PLATFORM_POLICY)
    ]


def get_platform_profile_status_matrix() -> list[dict[str, object]]:
    rows = [
        _without_selection_role_fields(row)
        for row in build_platform_profile_status_matrix(
            STRATEGY_CATALOG,
            policy=PLATFORM_POLICY,
            eligible_profiles=FIRSTRADE_ENABLED_PROFILES,
        )
    ]
    source_platform = get_strategy_adapter_source_platform()
    for row in rows:
        row["strategy_adapter_source_platform"] = source_platform
        row["runtime_note"] = (
            "enabled through value-native adapter shape pending first-class "
            "firstrade support in UsEquityStrategies"
        )
    return rows


def resolve_strategy_definition(
    raw_value: str | None,
    *,
    platform_id: str,
) -> StrategyDefinition:
    return resolve_platform_strategy_definition(
        raw_value,
        platform_id=platform_id,
        strategy_catalog=STRATEGY_CATALOG,
        policy=PLATFORM_POLICY,
    )


def resolve_strategy_metadata(
    raw_value: str | None,
    *,
    platform_id: str,
) -> StrategyMetadata:
    definition = resolve_strategy_definition(raw_value, platform_id=platform_id)
    return get_catalog_strategy_metadata(STRATEGY_CATALOG, definition.profile)

