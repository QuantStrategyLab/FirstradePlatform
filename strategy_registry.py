from __future__ import annotations

from quant_platform_kit.common.strategies import (
    PlatformCapabilityMatrix,
    PlatformStrategyPolicy,
    StrategyDefinition,
    StrategyMetadata,
    US_EQUITY_DOMAIN,
    build_platform_profile_matrix,
    build_platform_profile_status_matrix,
    derive_enabled_profiles_for_platform,
    derive_eligible_profiles_for_platform,
    get_catalog_strategy_metadata,
    get_enabled_profiles_for_platform,
    resolve_platform_strategy_definition,
)
from us_equity_strategies import (
    get_platform_runtime_adapter,
    get_runtime_enabled_profiles,
    get_strategy_catalog,
)

FIRSTRADE_PLATFORM = "firstrade"

PLATFORM_SUPPORTED_DOMAINS: dict[str, frozenset[str]] = {
    FIRSTRADE_PLATFORM: frozenset({US_EQUITY_DOMAIN}),
}

STRATEGY_CATALOG = get_strategy_catalog()
FIRSTRADE_ROLLOUT_ALLOWLIST = get_runtime_enabled_profiles()
PLATFORM_CAPABILITY_MATRIX = PlatformCapabilityMatrix(
    platform_id=FIRSTRADE_PLATFORM,
    supported_domains=PLATFORM_SUPPORTED_DOMAINS[FIRSTRADE_PLATFORM],
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
    supported_capabilities=frozenset(),
)
ELIGIBLE_STRATEGY_PROFILES = derive_eligible_profiles_for_platform(
    STRATEGY_CATALOG,
    capability_matrix=PLATFORM_CAPABILITY_MATRIX,
    runtime_adapter_loader=lambda profile: get_platform_runtime_adapter(
        profile,
        platform_id=FIRSTRADE_PLATFORM,
    ),
)
FIRSTRADE_ENABLED_PROFILES = derive_enabled_profiles_for_platform(
    STRATEGY_CATALOG,
    capability_matrix=PLATFORM_CAPABILITY_MATRIX,
    runtime_adapter_loader=lambda profile: get_platform_runtime_adapter(
        profile,
        platform_id=FIRSTRADE_PLATFORM,
    ),
    rollout_allowlist=FIRSTRADE_ROLLOUT_ALLOWLIST,
)
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


def get_eligible_profiles_for_platform(platform_id: str) -> frozenset[str]:
    if platform_id != FIRSTRADE_PLATFORM:
        return frozenset()
    return ELIGIBLE_STRATEGY_PROFILES


def get_supported_profiles_for_platform(platform_id: str) -> frozenset[str]:
    return get_enabled_profiles_for_platform(platform_id, policy=PLATFORM_POLICY)


def get_platform_profile_matrix() -> list[dict[str, object]]:
    return [
        _without_selection_role_fields(row)
        for row in build_platform_profile_matrix(STRATEGY_CATALOG, policy=PLATFORM_POLICY)
    ]


def get_platform_profile_status_matrix() -> list[dict[str, object]]:
    return [
        _without_selection_role_fields(row)
        for row in build_platform_profile_status_matrix(
            STRATEGY_CATALOG,
            policy=PLATFORM_POLICY,
            eligible_profiles=ELIGIBLE_STRATEGY_PROFILES,
        )
    ]


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
