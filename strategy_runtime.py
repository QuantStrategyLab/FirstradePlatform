from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from quant_platform_kit.common.feature_snapshot import load_feature_snapshot_guarded
from quant_platform_kit.common.feature_snapshot_runtime import (
    FeatureSnapshotRuntimeSettings,
    evaluate_feature_snapshot_strategy,
)
from quant_platform_kit.strategy_contracts import (
    StrategyDecision,
    StrategyEntrypoint,
    StrategyRuntimeAdapter,
    apply_runtime_policy_to_runtime_config,
    build_execution_timing_metadata,
    build_strategy_context_from_available_inputs,
)
from runtime_config_support import PlatformRuntimeSettings
from strategy_loader import (
    load_strategy_entrypoint_for_profile,
    load_strategy_runtime_adapter_for_profile,
)

_FEATURE_SNAPSHOT_INPUT = "feature_snapshot"


@dataclass(frozen=True)
class StrategyEvaluationResult:
    decision: StrategyDecision
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedStrategyRuntime:
    entrypoint: StrategyEntrypoint
    runtime_adapter: StrategyRuntimeAdapter
    runtime_settings: PlatformRuntimeSettings
    runtime_overrides: Mapping[str, Any] = field(default_factory=dict)
    runtime_config: Mapping[str, Any] = field(default_factory=dict)
    merged_runtime_config: Mapping[str, Any] = field(default_factory=dict)
    logger: Callable[[str], None] = print

    @property
    def profile(self) -> str:
        return self.entrypoint.manifest.profile

    @property
    def display_name(self) -> str:
        return str(self.entrypoint.manifest.display_name)

    @property
    def managed_symbols(self) -> tuple[str, ...]:
        configured = self.merged_runtime_config.get("managed_symbols", ())
        return tuple(str(symbol) for symbol in configured)

    def evaluate(
        self,
        *,
        translator: Callable[[str], str],
        signal_text_fn: Callable[[str], str] | None = None,
        **available_inputs,
    ) -> StrategyEvaluationResult:
        runtime_config = dict(self.runtime_overrides)
        runtime_config.setdefault("translator", translator)
        if signal_text_fn is not None:
            runtime_config.setdefault("signal_text_fn", signal_text_fn)
        apply_runtime_policy_to_runtime_config(runtime_config, self.runtime_adapter)

        if _FEATURE_SNAPSHOT_INPUT in frozenset(self.entrypoint.manifest.required_inputs):
            return self._evaluate_feature_snapshot_strategy(
                runtime_config=runtime_config,
                available_inputs=available_inputs,
            )

        as_of = datetime.now(timezone.utc)
        ctx = build_strategy_context_from_available_inputs(
            entrypoint=self.entrypoint,
            runtime_adapter=self.runtime_adapter,
            as_of=as_of,
            available_inputs=available_inputs,
            runtime_config=runtime_config,
        )
        decision = self.entrypoint.evaluate(ctx)
        return StrategyEvaluationResult(
            decision=decision,
            metadata={
                "strategy_profile": self.profile,
                "strategy_display_name": self.display_name,
                **build_execution_timing_metadata(
                    signal_date=as_of,
                    signal_effective_after_trading_days=(
                        self.runtime_adapter.runtime_policy.signal_effective_after_trading_days
                    ),
                ),
            },
        )

    def _evaluate_feature_snapshot_strategy(
        self,
        *,
        runtime_config: Mapping[str, Any],
        available_inputs: Mapping[str, Any],
    ) -> StrategyEvaluationResult:
        runtime_config = dict(runtime_config)
        runtime_config.setdefault("run_as_of", datetime.now(timezone.utc).replace(tzinfo=None))
        result = evaluate_feature_snapshot_strategy(
            entrypoint=self.entrypoint,
            runtime_adapter=self.runtime_adapter,
            runtime_settings=FeatureSnapshotRuntimeSettings(
                feature_snapshot_path=self.runtime_settings.feature_snapshot_path,
                feature_snapshot_manifest_path=self.runtime_settings.feature_snapshot_manifest_path,
                strategy_config_path=self.runtime_settings.strategy_config_path,
                strategy_config_source=self.runtime_settings.strategy_config_source,
                dry_run_only=self.runtime_settings.dry_run_only,
            ),
            runtime_config=runtime_config,
            merged_runtime_config=self.merged_runtime_config,
            available_inputs=available_inputs,
            base_managed_symbols=self.managed_symbols,
            include_strategy_display_name=True,
            set_run_as_of=True,
            snapshot_loader=load_feature_snapshot_guarded,
        )
        return StrategyEvaluationResult(
            decision=result.decision,
            metadata=result.metadata,
        )

    def load_runtime_parameters(self) -> dict[str, Any]:
        runtime_loader = self.runtime_adapter.runtime_parameter_loader
        if not callable(runtime_loader):
            return {}
        return dict(
            runtime_loader(
                config_path=self.runtime_settings.strategy_config_path,
                logger=self.logger,
            )
            or {}
        )


def _build_runtime_overrides(profile: str, runtime_settings: PlatformRuntimeSettings) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if profile == "tqqq_growth_income":
        if runtime_settings.income_threshold_usd is not None:
            overrides["income_threshold_usd"] = runtime_settings.income_threshold_usd
        if runtime_settings.qqqi_income_ratio is not None:
            overrides["qqqi_income_ratio"] = runtime_settings.qqqi_income_ratio
    if profile == "tech_communication_pullback_enhancement":
        if runtime_settings.runtime_execution_window_trading_days is not None:
            overrides["runtime_execution_window_trading_days"] = (
                runtime_settings.runtime_execution_window_trading_days
            )
    return overrides


def load_strategy_runtime(
    raw_profile: str | None,
    *,
    runtime_settings: PlatformRuntimeSettings,
    runtime_overrides: Mapping[str, Any] | None = None,
    logger: Callable[[str], None] = print,
) -> LoadedStrategyRuntime:
    entrypoint = load_strategy_entrypoint_for_profile(raw_profile)
    runtime_adapter = load_strategy_runtime_adapter_for_profile(raw_profile)
    overrides = _build_runtime_overrides(entrypoint.manifest.profile, runtime_settings)
    overrides.update(runtime_overrides or {})
    runtime = LoadedStrategyRuntime(
        entrypoint=entrypoint,
        runtime_adapter=runtime_adapter,
        runtime_settings=runtime_settings,
        runtime_overrides=overrides,
        logger=logger,
    )
    runtime_config = runtime.load_runtime_parameters()
    merged_runtime_config = dict(entrypoint.manifest.default_config)
    merged_runtime_config.update(runtime_config)
    merged_runtime_config.update(overrides)
    return LoadedStrategyRuntime(
        entrypoint=entrypoint,
        runtime_adapter=runtime_adapter,
        runtime_settings=runtime_settings,
        runtime_overrides=overrides,
        runtime_config=runtime_config,
        merged_runtime_config=merged_runtime_config,
        logger=logger,
    )

