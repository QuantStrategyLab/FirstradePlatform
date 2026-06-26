from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from application.firstrade_client import FirstradeCredentials
from application.rebalance_service import _runtime_metadata_with_execution_policy, run_strategy_cycle
from notifications.telegram import I18N, build_translator, render_cycle_summary
from quant_platform_kit.strategy_contracts import PositionTarget, StrategyDecision
from runtime_config_support import PlatformRuntimeSettings


def _runtime_settings() -> PlatformRuntimeSettings:
    return PlatformRuntimeSettings(
        project_id=None,
        account_prefix="FIRSTRADE",
        account_region="US",
        strategy_profile="tqqq_growth_income",
        strategy_display_name="TQQQ Growth Income",
        strategy_domain="us_equity",
        notify_lang="en",
        tg_token=None,
        tg_chat_id=None,
        dry_run_only=True,
        live_trading_enabled=False,
        run_strategy_on_http=False,
        live_order_ack=False,
        max_order_notional_usd=25.0,
    )


def _runtime_settings_with_persistence(**overrides) -> PlatformRuntimeSettings:
    base = _runtime_settings()
    values = dict(base.__dict__)
    values.update(overrides)
    return PlatformRuntimeSettings(**values)


def test_runtime_metadata_uses_platform_execution_policy_over_strategy_metadata():
    metadata = {
        "signal": "ok",
        "firstrade_execution_policy": {
            "reserved_cash_floor_usd": 1.0,
            "reserved_cash_ratio": 0.0,
        },
    }

    result = _runtime_metadata_with_execution_policy(
        metadata,
        settings=_runtime_settings_with_persistence(
            reserved_cash_floor_usd=250.0,
            reserved_cash_ratio=0.03,
        ),
    )

    assert result == {
        "signal": "ok",
        "firstrade_execution_policy": {
            "reserved_cash_floor_usd": 250.0,
            "reserved_cash_ratio": 0.03,
        },
    }


class FakeFirstradeClient:
    def __init__(self, _credentials, *, live_trading_enabled=False):
        self.live_trading_enabled = live_trading_enabled
        self.orders = []

    def connect(self):
        return self

    def select_account(self, requested_account=None):
        return requested_account or "12345678"

    def get_balances(self, _account):
        return {"total_value": "1000.00", "cash": "1000.00", "buying_power": "1000.00"}

    def get_positions(self, _account):
        return {"items": []}

    def get_quote(self, _account, symbol):
        return {"symbol": symbol, "last": "10.00", "bid": "9.90", "ask": "10.10"}

    def get_ohlc(self, _symbol, _range):
        return [(1700000000000 + index * 86400000, 9, 11, 8, 10 + index, 1000) for index in range(5)]

    def place_stock_order(self, request, dry_run=True, explicit_live_ack=False):
        self.orders.append((request, dry_run, explicit_live_ack))
        return {
            "preview": dry_run,
            "symbol": request.symbol,
            "quantity": request.quantity,
            "price_type": request.price_type,
        }


class FakeStrategyRuntime:
    profile = "tqqq_growth_income"
    display_name = "TQQQ Growth Income"
    managed_symbols = ("AAA",)
    runtime_adapter = SimpleNamespace(available_inputs=frozenset({"portfolio_snapshot"}))
    merged_runtime_config = {"benchmark_symbol": "QQQ"}

    def evaluate(self, **inputs):
        assert "portfolio_snapshot" in inputs
        return SimpleNamespace(
            decision=StrategyDecision(
                positions=(
                    PositionTarget(symbol="AAA", target_value=50.0, role="risk"),
                ),
                diagnostics={"execution_annotations": {"trade_threshold_value": 1.0}},
            ),
            metadata={"strategy_profile": self.profile},
        )


class FakeStateStore:
    def __init__(self):
        self.payloads = {}
        self.writes = []

    def read_json(self, key):
        return self.payloads.get(key)

    def write_json(self, key, payload):
        self.payloads[key] = dict(payload)
        self.writes.append((key, dict(payload)))
        return True


def _latest_strategy_run_payloads(store: FakeStateStore) -> list[dict]:
    return [payload for key, payload in store.writes if key.endswith("latest.json")]


def test_notification_i18n_keys_are_aligned():
    assert set(I18N["zh"]) == set(I18N["en"])
    assert build_translator("zh")("account_label", account="****1234") == "🆔 账户: ****1234"
    assert build_translator("en")("account_label", account="****1234") == "🆔 Account: ****1234"
    zh = build_translator("zh")
    assert (
        zh(
            "blend_gate_reason_volatility_delever_dynamic",
            symbol="SOXX",
            window=10,
            volatility="61.0%",
            threshold="60.0%",
            threshold_detail=zh(
                "blend_gate_volatility_threshold_detail_dynamic",
                percentile="p95",
                lookback="252",
                floor="50.0%",
                cap="75.0%",
                sample_count="252",
            ),
            redirect_symbol="SOXX",
        )
        == "SOXX 10 日年化波动率 61.0% 高于实际阈值 60.0%（动态 p95，252日窗口，范围 50.0%-75.0%，样本 252），SOXL 转向 SOXX"
    )
    assert (
        zh(
            "strategy_plugin_line",
            plugin=zh("strategy_plugin_name_market_regime_control"),
            enabled=zh("strategy_plugin_enabled_true"),
            mode=zh("strategy_plugin_mode_shadow"),
            route=zh("strategy_plugin_route_risk_reduced"),
            action=zh("strategy_plugin_action_delever"),
        )
        == "🧩 插件：市场状态控制 | 启用：是 | 状态：风险降低 | 提醒：降杠杆"
    )
    assert "策略侧已批准" in zh("strategy_plugin_guidance_market_regime_control_risk_reduced_delever")
    en = build_translator("en")
    assert en("strategy_plugin_name_market_regime_control") == "Market Regime Control"
    assert (
        en(
            "blend_gate_reason_volatility_delever_dynamic",
            symbol="SOXX",
            window=10,
            volatility="61.0%",
            threshold="60.0%",
            threshold_detail=en(
                "blend_gate_volatility_threshold_detail_dynamic",
                percentile="p95",
                lookback="252",
                floor="50.0%",
                cap="75.0%",
                sample_count="252",
            ),
            redirect_symbol="SOXX",
        )
        == "SOXX 10d annualized volatility 61.0% is above effective threshold 60.0% (dynamic p95, 252d lookback, bounded 50.0%-75.0%, samples 252); redirect SOXL to SOXX"
    )


def test_run_strategy_cycle_builds_dry_run_order(monkeypatch):
    observed = {}
    messages = []

    def fake_client_factory(*args, **kwargs):
        client = FakeFirstradeClient(*args, **kwargs)
        observed["client"] = client
        return client

    monkeypatch.setattr(
        "application.rebalance_service.load_strategy_runtime",
        lambda *_args, **_kwargs: FakeStrategyRuntime(),
    )

    result = run_strategy_cycle(
        runtime_settings=_runtime_settings(),
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=fake_client_factory,
        notification_sender=messages.append,
        env_reader=lambda _name, default=None: default,
    )

    assert result["ok"] is True
    assert result["dry_run_only"] is True
    assert result["action_done"] is True
    assert result["submitted_orders"] == [
        {
            "symbol": "AAA",
            "side": "buy",
            "quantity": 2.0,
            "order_type": "limit",
            "limit_price": 10.05,
            "status": "previewed",
            "broker_order_id": None,
            "raw_payload": {
                "preview": True,
                "symbol": "AAA",
                "quantity": 2,
                "price_type": "limit",
            },
        }
    ]
    request, dry_run, explicit_live_ack = observed["client"].orders[0]
    assert request.limit_price == 10.05
    assert dry_run is True
    assert explicit_live_ack is False
    assert result["notification_sent"] is True
    assert "🔔 【Rebalance Instruction】" in messages[0]
    assert "🧭 Strategy: TQQQ Growth Income" in messages[0]
    assert "🆔 Account: ****5678" in messages[0]
    assert "📌 Strategy Account" in messages[0]
    assert "Target changes: AAA +50.00 USD" in messages[0]
    assert "🧾 Execution details" in messages[0]
    assert "🧪 Dry-run limit buy AAA: 2 shares @ $10.05" in messages[0]


def test_run_strategy_cycle_translates_weight_targets_when_balance_total_missing(monkeypatch):
    class CashOnlyClient(FakeFirstradeClient):
        def get_balances(self, _account):
            return {"cash_balance": "$1000.00", "buying_power": "$1000.00"}

    class WeightTargetRuntime(FakeStrategyRuntime):
        profile = "russell_top50_leader_rotation"
        display_name = "Russell Top50 Leader Rotation"

        def evaluate(self, **inputs):
            assert "portfolio_snapshot" in inputs
            return SimpleNamespace(
                decision=StrategyDecision(
                    positions=(
                        PositionTarget(symbol="AAA", target_weight=0.5, role="risk"),
                    ),
                    diagnostics={},
                ),
                metadata={"strategy_profile": self.profile},
            )

    monkeypatch.setattr(
        "application.rebalance_service.load_strategy_runtime",
        lambda *_args, **_kwargs: WeightTargetRuntime(),
    )

    result = run_strategy_cycle(
        runtime_settings=_runtime_settings_with_persistence(
            strategy_profile="russell_top50_leader_rotation",
            strategy_display_name="Russell Top50 Leader Rotation",
        ),
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=CashOnlyClient,
        env_reader=lambda _name, default=None: default,
    )

    assert result["ok"] is True
    assert result["portfolio"]["total_equity"] == 1000.0
    assert result["allocation"]["targets"]["AAA"] == 500.0
    assert result["submitted_orders"][0]["symbol"] == "AAA"


def test_run_strategy_cycle_no_executes_weight_targets_when_total_equity_zero(monkeypatch):
    class ZeroEquityClient(FakeFirstradeClient):
        def get_balances(self, _account):
            return {"total_value": "$0.00", "cash_balance": "$0.00", "buying_power": "$0.00"}

    class WeightTargetRuntime(FakeStrategyRuntime):
        profile = "russell_top50_leader_rotation"
        display_name = "Russell Top50 Leader Rotation"

        def evaluate(self, **inputs):
            assert "portfolio_snapshot" in inputs
            return SimpleNamespace(
                decision=StrategyDecision(
                    positions=(
                        PositionTarget(symbol="AAA", target_weight=0.5, role="risk"),
                    ),
                    diagnostics={},
                ),
                metadata={"strategy_profile": self.profile},
            )

    monkeypatch.setattr(
        "application.rebalance_service.load_strategy_runtime",
        lambda *_args, **_kwargs: WeightTargetRuntime(),
    )

    result = run_strategy_cycle(
        runtime_settings=_runtime_settings_with_persistence(
            strategy_profile="russell_top50_leader_rotation",
            strategy_display_name="Russell Top50 Leader Rotation",
        ),
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=ZeroEquityClient,
        env_reader=lambda _name, default=None: default,
    )

    assert result["ok"] is True
    assert result["portfolio"]["total_equity"] == 0.0
    assert result["allocation"]["targets"]["AAA"] == 0.0
    assert result["submitted_orders"] == []
    assert result["skipped_orders"] == [
        {"symbol": "AAA", "reason": "below_trade_threshold", "delta_value": 0.0}
    ]


def test_run_strategy_cycle_loads_strategy_plugin_report_and_sends_email(
    monkeypatch,
    tmp_path,
):
    signal_path = tmp_path / "latest_signal.json"
    signal_path.write_text(
        json.dumps(
            {
                "strategy": "tqqq_growth_income",
                "plugin": "crisis_response_shadow",
                "mode": "shadow",
                "configured_mode": "shadow",
                "effective_mode": "shadow",
                "schema_version": "crisis_response_shadow.v1",
                "as_of": "2026-05-24",
                "canonical_route": "true_crisis",
                "suggested_action": "defend",
                "would_trade_if_enabled": True,
                "execution_controls": {},
            }
        ),
        encoding="utf-8",
    )
    mount_config = json.dumps(
        {
            "strategy_plugins": [
                {
                    "strategy": "tqqq_growth_income",
                    "plugin": "crisis_response_shadow",
                    "signal_path": str(signal_path),
                }
            ]
        }
    )
    settings = _runtime_settings_with_persistence(
        strategy_plugin_mounts_json=mount_config,
        strategy_plugin_alert_email_recipients=("voice@example.com",),
        strategy_plugin_alert_email_sender_email="bot@example.com",
        strategy_plugin_alert_email_sender_password="app-password",
    )
    messages = []
    observed_alerts = []

    monkeypatch.setattr(
        "application.rebalance_service.load_strategy_runtime",
        lambda *_args, **_kwargs: FakeStrategyRuntime(),
    )

    def fake_dispatch(signals, **kwargs):
        observed_alerts.append((tuple(signals), kwargs))
        return SimpleNamespace(
            to_report_fields=lambda: {
                "strategy_plugin_alert_attempted_count": 2,
                "strategy_plugin_alert_sent_count": 2,
                "strategy_plugin_alert_skipped_count": 0,
                "strategy_plugin_alert_failed_count": 0,
                "strategy_plugin_alert_email_attempted_count": 1,
                "strategy_plugin_alert_email_sent_count": 1,
                "strategy_plugin_alert_email_skipped_count": 0,
                "strategy_plugin_alert_email_failed_count": 0,
                "strategy_plugin_alert_email_deliveries": [
                    {"subject": "Crisis plugin alert", "status": "sent"}
                ],
                "strategy_plugin_alert_sms_attempted_count": 1,
                "strategy_plugin_alert_sms_sent_count": 1,
                "strategy_plugin_alert_sms_skipped_count": 0,
                "strategy_plugin_alert_sms_failed_count": 0,
                "strategy_plugin_alert_sms_deliveries": [
                    {"subject": "Crisis plugin alert", "status": "sent"}
                ],
            },
        )

    monkeypatch.setattr("application.rebalance_service.dispatch_strategy_plugin_alerts", fake_dispatch)

    result = run_strategy_cycle(
        runtime_settings=settings,
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FakeFirstradeClient,
        notification_sender=messages.append,
        env_reader=lambda _name, default=None: default,
    )

    assert result["strategy_plugins"][0]["canonical_route"] == "true_crisis"
    assert result["strategy_plugin_alert_email_sent_count"] == 1
    assert result["strategy_plugin_alert_sms_sent_count"] == 1
    assert "strategy_plugin_lines" not in result
    assert len(observed_alerts) == 1
    assert observed_alerts[0][0][0].canonical_route == "true_crisis"
    assert "firstrade" in observed_alerts[0][1]["context_label"]
    assert observed_alerts[0][1]["notification_settings"] is settings
    assert observed_alerts[0][1]["state_settings"] is not None
    assert result["strategy_plugin_alert_email_deliveries"][0]["status"] == "sent"
    assert result["strategy_plugin_alert_sms_deliveries"][0]["status"] == "sent"
    assert "🧩 Plugin:" not in messages[0]


def test_run_strategy_cycle_strategy_plugin_load_error_is_non_blocking(monkeypatch):
    settings = _runtime_settings_with_persistence(strategy_plugin_mounts_json="{bad-json")

    monkeypatch.setattr(
        "application.rebalance_service.load_strategy_runtime",
        lambda *_args, **_kwargs: FakeStrategyRuntime(),
    )

    result = run_strategy_cycle(
        runtime_settings=settings,
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FakeFirstradeClient,
        env_reader=lambda _name, default=None: default,
    )

    assert result["ok"] is True
    assert result["action_done"] is True
    assert result["strategy_plugin_error"].startswith("JSONDecodeError:")
    assert result["strategy_plugin_error_lines"] == (
        "⚠️ Plugin signal failed to load: invalid plugin mount JSON; this run falls back to built-in strategy rules",
        "🧩 Plugin impact this run: no usable plugin signal loaded",
    )
    assert result["strategy_plugin_alert_email_sent_count"] == 0
    assert result["strategy_plugin_alert_sms_sent_count"] == 0


def test_run_strategy_cycle_persists_strategy_run_state(monkeypatch):
    store = FakeStateStore()

    monkeypatch.setattr(
        "application.rebalance_service.load_strategy_runtime",
        lambda *_args, **_kwargs: FakeStrategyRuntime(),
    )

    result = run_strategy_cycle(
        runtime_settings=_runtime_settings_with_persistence(persist_strategy_runs=True),
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FakeFirstradeClient,
        state_store=store,
        env_reader=lambda _name, default=None: default,
    )

    stages = [payload["stage"] for _key, payload in store.writes if _key.endswith("latest.json")]
    assert stages == ["ORDERS_PLANNED", "DRY_RUN_COMPLETED"]
    assert result["strategy_run_persisted"] is True
    assert result["strategy_run_period"]
    assert result["strategy_run_stage"] == "DRY_RUN_COMPLETED"
    latest_payload = store.writes[-2][1]
    assert latest_payload["stage"] == "DRY_RUN_COMPLETED"
    assert latest_payload["submitted_orders"][0]["symbol"] == "AAA"
    assert latest_payload["plan"]["allocation"]["targets"]["AAA"] == 50.0


def test_run_strategy_cycle_skips_duplicate_live_monthly_run(monkeypatch):
    store = FakeStateStore()
    settings = _runtime_settings_with_persistence(
        dry_run_only=False,
        live_trading_enabled=True,
        live_order_ack=True,
        persist_strategy_runs=True,
    )
    key = "strategy-runs/____5678/tqqq_growth_income/2026_05/latest.json"
    store.payloads[key] = {
        "stage": "SUBMITTED",
        "as_of": "2026-05-01T01:02:03+00:00",
        "dry_run_only": False,
    }
    observed = {}

    def fake_client_factory(*args, **kwargs):
        client = FakeFirstradeClient(*args, **kwargs)
        observed["client"] = client
        return client

    monkeypatch.setattr(
        "application.rebalance_service.load_strategy_runtime",
        lambda *_args, **_kwargs: FakeStrategyRuntime(),
    )
    monkeypatch.setattr(
        "application.rebalance_service._utcnow",
        lambda: datetime(2026, 5, 15, tzinfo=timezone.utc),
    )

    result = run_strategy_cycle(
        runtime_settings=settings,
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=fake_client_factory,
        state_store=store,
        env_reader=lambda _name, default=None: default,
    )

    assert result["idempotency_skipped"] is True
    assert result["action_done"] is False
    assert result["strategy_run_stage"] == "SUBMITTED"
    assert result["strategy_run_persisted"] is True
    assert observed["client"].orders == []
    latest_payloads = _latest_strategy_run_payloads(store)
    assert len(latest_payloads) == 1
    assert latest_payloads[0]["stage"] == "SUBMITTED"
    assert latest_payloads[0]["idempotency_skipped"] is True
    assert latest_payloads[0]["existing_strategy_run_stage"] == "SUBMITTED"
    assert latest_payloads[0]["skipped_orders"] == [
        {"reason": "duplicate_live_strategy_run", "run_period": "2026-05"}
    ]
    assert len(store.writes) == 2


def test_run_strategy_cycle_skips_duplicate_live_monthly_no_action(monkeypatch):
    store = FakeStateStore()
    settings = _runtime_settings_with_persistence(
        dry_run_only=False,
        live_trading_enabled=True,
        live_order_ack=True,
        persist_strategy_runs=True,
    )
    key = "strategy-runs/____5678/tqqq_growth_income/2026_05/latest.json"
    store.payloads[key] = {
        "stage": "NO_ACTION",
        "as_of": "2026-05-01T01:02:03+00:00",
        "dry_run_only": False,
    }
    observed = {}

    def fake_client_factory(*args, **kwargs):
        client = FakeFirstradeClient(*args, **kwargs)
        observed["client"] = client
        return client

    monkeypatch.setattr(
        "application.rebalance_service.load_strategy_runtime",
        lambda *_args, **_kwargs: FakeStrategyRuntime(),
    )
    monkeypatch.setattr(
        "application.rebalance_service._utcnow",
        lambda: datetime(2026, 5, 15, tzinfo=timezone.utc),
    )

    result = run_strategy_cycle(
        runtime_settings=settings,
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=fake_client_factory,
        state_store=store,
        env_reader=lambda _name, default=None: default,
    )

    assert result["idempotency_skipped"] is True
    assert result["existing_strategy_run_stage"] == "NO_ACTION"
    assert result["action_done"] is False
    assert result["strategy_run_stage"] == "NO_ACTION"
    assert result["strategy_run_persisted"] is True
    assert observed["client"].orders == []
    latest_payloads = _latest_strategy_run_payloads(store)
    assert len(latest_payloads) == 1
    assert latest_payloads[0]["stage"] == "NO_ACTION"
    assert latest_payloads[0]["idempotency_skipped"] is True
    assert latest_payloads[0]["existing_strategy_run_stage"] == "NO_ACTION"
    assert latest_payloads[0]["skipped_orders"] == [
        {"reason": "duplicate_live_strategy_run", "run_period": "2026-05"}
    ]
    assert len(store.writes) == 2


def test_run_strategy_cycle_persists_live_execution_blocked_without_terminal_stage(monkeypatch):
    store = FakeStateStore()
    settings = _runtime_settings_with_persistence(
        dry_run_only=False,
        live_trading_enabled=True,
        live_order_ack=True,
        persist_strategy_runs=True,
        max_order_notional_usd=1.0,
    )

    monkeypatch.setattr(
        "application.rebalance_service.load_strategy_runtime",
        lambda *_args, **_kwargs: FakeStrategyRuntime(),
    )

    result = run_strategy_cycle(
        runtime_settings=settings,
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FakeFirstradeClient,
        state_store=store,
        env_reader=lambda _name, default=None: default,
    )

    latest_payload = store.writes[-2][1]
    assert result["action_done"] is False
    assert result["ok"] is False
    assert result["execution_blocked"] is True
    assert result["execution_block_retryable"] is True
    assert result["strategy_run_stage"] == "EXECUTION_BLOCKED"
    assert latest_payload["stage"] == "EXECUTION_BLOCKED"


def test_run_strategy_cycle_persists_live_funding_block_as_terminal(monkeypatch):
    store = FakeStateStore()
    settings = _runtime_settings_with_persistence(
        dry_run_only=False,
        live_trading_enabled=True,
        live_order_ack=True,
        persist_strategy_runs=True,
        max_order_notional_usd=None,
    )

    class FundingBlockedClient(FakeFirstradeClient):
        def get_balances(self, _account):
            return {"total_value": "150.00", "cash": "50.00", "buying_power": "50.00"}

        def get_quote(self, _account, symbol):
            return {"symbol": symbol, "last": "100.00", "bid": "99.90", "ask": "100.10"}

    class FundingBlockedRuntime(FakeStrategyRuntime):
        def evaluate(self, **inputs):
            assert "portfolio_snapshot" in inputs
            return SimpleNamespace(
                decision=StrategyDecision(
                    positions=(
                        PositionTarget(symbol="AAA", target_value=150.0, role="risk"),
                    ),
                    diagnostics={"execution_annotations": {"trade_threshold_value": 1.0}},
                ),
                metadata={"strategy_profile": self.profile},
            )

    monkeypatch.setattr(
        "application.rebalance_service.load_strategy_runtime",
        lambda *_args, **_kwargs: FundingBlockedRuntime(),
    )

    result = run_strategy_cycle(
        runtime_settings=settings,
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FundingBlockedClient,
        state_store=store,
        env_reader=lambda _name, default=None: default,
    )

    latest_payload = store.writes[-2][1]
    assert result["action_done"] is False
    assert result["ok"] is False
    assert result["execution_blocked"] is True
    assert result["execution_block_retryable"] is False
    assert result["funding_blocked"] is True
    assert result["strategy_run_stage"] == "FUNDING_BLOCKED"
    assert result["skipped_orders"][0]["reason"] == "insufficient_cash_for_whole_share"
    assert latest_payload["stage"] == "FUNDING_BLOCKED"

    write_count = len(store.writes)
    second_result = run_strategy_cycle(
        runtime_settings=settings,
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FundingBlockedClient,
        state_store=store,
        env_reader=lambda _name, default=None: default,
    )

    assert second_result["idempotency_skipped"] is True
    assert second_result["existing_strategy_run_stage"] == "FUNDING_BLOCKED"
    assert second_result["strategy_run_stage"] == "FUNDING_BLOCKED"
    assert second_result["strategy_run_persisted"] is True
    assert len(store.writes) == write_count + 2
    latest_payloads = _latest_strategy_run_payloads(store)
    duplicate_payload = latest_payloads[-1]
    assert duplicate_payload["stage"] == "FUNDING_BLOCKED"
    assert duplicate_payload["idempotency_skipped"] is True
    assert duplicate_payload["existing_strategy_run_stage"] == "FUNDING_BLOCKED"
    assert duplicate_payload["skipped_orders"][0]["reason"] == "duplicate_live_strategy_run"


def test_run_strategy_cycle_persists_live_partial_submission_as_non_terminal(monkeypatch):
    store = FakeStateStore()
    settings = _runtime_settings_with_persistence(
        dry_run_only=False,
        live_trading_enabled=True,
        live_order_ack=True,
        persist_strategy_runs=True,
        max_order_notional_usd=1000.0,
    )

    class PartialRuntime(FakeStrategyRuntime):
        managed_symbols = ("AAA", "BBB")

        def evaluate(self, **inputs):
            assert "portfolio_snapshot" in inputs
            return SimpleNamespace(
                decision=StrategyDecision(
                    positions=(
                        PositionTarget(symbol="AAA", target_value=50.0, role="risk"),
                        PositionTarget(symbol="BBB", target_value=150.0, role="risk"),
                    ),
                    diagnostics={"execution_annotations": {"trade_threshold_value": 1.0}},
                ),
                metadata={"strategy_profile": self.profile},
            )

    class PartialClient(FakeFirstradeClient):
        def get_balances(self, _account):
            return {"total_value": "100.00", "cash": "60.00", "buying_power": "60.00"}

        def get_quote(self, _account, symbol):
            prices = {"AAA": "10.00", "BBB": "100.00"}
            return {"symbol": symbol, "last": prices[symbol], "bid": "9.90", "ask": "10.10"}

    monkeypatch.setattr(
        "application.rebalance_service.load_strategy_runtime",
        lambda *_args, **_kwargs: PartialRuntime(),
    )

    result = run_strategy_cycle(
        runtime_settings=settings,
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=PartialClient,
        state_store=store,
        env_reader=lambda _name, default=None: default,
    )

    latest_payload = store.writes[-2][1]
    assert result["action_done"] is True
    assert result["ok"] is False
    assert result["execution_blocked"] is True
    assert result["strategy_run_stage"] == "PARTIAL_SUBMITTED"
    assert latest_payload["stage"] == "PARTIAL_SUBMITTED"


def test_render_cycle_summary_formats_skipped_orders_in_unified_chinese_template():
    message = render_cycle_summary(
        {
            "account": "****1234",
            "strategy_profile": "tqqq_growth_income",
            "strategy_display_name": "TQQQ 增长收益",
            "dry_run_only": False,
            "portfolio": {
                "total_equity": 98.65,
                "liquid_cash": 98.65,
                "portfolio_rows": (("TQQQ", "QQQ"), ("BOXX",)),
                "market_values": {"TQQQ": 0.0, "QQQ": 0.0, "BOXX": 0.0},
                "quantities": {"TQQQ": 0, "QQQ": 0, "BOXX": 0},
            },
            "allocation": {"targets": {"TQQQ": 44.39, "QQQ": 44.39, "BOXX": 7.89}},
            "execution": {
                "reserved_cash": 1.97,
                "investable_cash": 96.68,
                "signal_display": "entry",
                "signal_date": "2026-05-20",
                "effective_date": "2026-05-21",
                "execution_timing_contract": "next_trading_day",
            },
            "submitted_orders": [],
            "skipped_orders": [
                {"symbol": "TQQQ", "reason": "buy_quantity_zero"},
                {"symbol": "QQQ", "reason": "buy_quantity_zero"},
                {"symbol": "BOXX", "reason": "below_trade_threshold"},
            ],
        },
        lang="zh",
    )

    assert "🔔 【调仓指令】" in message
    assert "🆔 账户: ****1234" in message
    assert "📌 策略账户概览" in message
    assert "⏱ 执行时点: 2026-05-20 -> 2026-05-21 (次一交易日执行)" in message
    assert "🎯 信号: 入场信号" in message
    assert "调仓变化: BOXX +7.89 USD, QQQ +44.39 USD, TQQQ +44.39 USD" in message
    assert "🧾 执行明细" in message
    assert "未下单: 原因=TQQQ,QQQ（整数股不足 1 股，无需下单）, BOXX（低于调仓阈值）" in message
    assert "profile:" not in message
    assert "targets:" not in message


def test_render_cycle_summary_localizes_strategy_signal_codes():
    message = render_cycle_summary(
        {
            "account": "****1234",
            "strategy_profile": "soxl_soxx_trend_income",
            "strategy_display_name": "SOXL/SOXX 半导体趋势收益",
            "dry_run_only": True,
            "portfolio": {
                "total_equity": 0.0,
                "liquid_cash": 0.0,
                "portfolio_rows": (("SOXL", "SOXX", "BOXX"), ("QQQI", "SPYI")),
                "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0, "QQQI": 0.0, "SPYI": 0.0},
                "quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 0, "QQQI": 0, "SPYI": 0},
            },
            "allocation": {"targets": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0, "QQQI": 0.0, "SPYI": 0.0}},
            "execution": {
                "reserved_cash": 0.0,
                "investable_cash": 0.0,
                "dashboard_text": "\n".join(
                    (
                        "📌 策略账户概览",
                        "💼 策略持仓",
                        "  - SOXL: $0.00 / 0股",
                        "🎯 信号: signal_blend_gate_risk_on: soxl_ratio=70.0%, soxx_ratio=20.0%, trend_symbol=SOXX, window=140",
                    )
                ),
                "status_display": "market_status_blend_gate_risk_on: asset=SOXX+SOXL",
                "signal_display": "signal_blend_gate_risk_on: soxl_ratio=70.0%, soxx_ratio=20.0%, trend_symbol=SOXX, window=140 | small_account_warning_note: min_recommended_equity=$1,000, portfolio_equity=$0, reason=integer-share minimum position sizing may prevent backtest replication",
                "signal_date": "2026-05-23",
                "effective_date": "2026-05-25",
                "execution_timing_contract": "next_trading_day",
            },
            "submitted_orders": [],
            "skipped_orders": [],
        },
        lang="zh",
    )

    assert "📊 市场状态: 🚀 风险开启（SOXX+SOXL）" in message
    assert "🎯 信号: SOXX 站上 140 日门槛线，持有 SOXL 70.0% + SOXX 20.0%" in message
    assert "  - 小账户提示：净值 $0 低于建议 $1,000；整数股和最小仓位限制可能导致实盘无法完全复现回测" in message
    assert message.count("🎯 信号:") == 1
    assert "signal_blend_gate_risk_on" not in message
    assert "soxl_ratio" not in message
    assert "small_account_warning_note" not in message


def test_render_cycle_summary_includes_small_account_cash_note_zh():
    message = render_cycle_summary(
        {
            "account": "****1234",
            "strategy_profile": "soxl_soxx_trend_income",
            "strategy_display_name": "SOXL/SOXX 半导体趋势收益",
            "dry_run_only": False,
            "portfolio": {
                "total_equity": 1294.0,
                "liquid_cash": 1294.0,
                "portfolio_rows": (("SOXL", "SOXX"), ("BOXX",)),
                "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0},
                "quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 0},
            },
            "allocation": {"targets": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0}},
            "execution": {
                "reserved_cash": 38.82,
                "investable_cash": 1255.18,
                "signal_date": "2026-05-26",
                "effective_date": "2026-05-27",
                "execution_timing_contract": "next_trading_day",
            },
            "submitted_orders": [],
            "skipped_orders": [],
            "execution_notes": [
                {
                    "symbol": "SOXX",
                    "target_value": 194.10,
                    "price": 525.0,
                    "cash_symbols": ("BOXX",),
                },
                {
                    "kind": "small_account_allocation_drift",
                    "symbol": "SOXX",
                    "target_weight": 0.15,
                    "projected_weight": 0.0,
                    "drift_weight": -0.15,
                },
            ],
        },
        lang="zh",
    )

    assert "ℹ️ [买入说明] SOXX.US 目标金额 $194.10 低于 1 股价格 $525.00" in message
    assert "小账户本轮保留现金，不回补 BOXX.US" in message
    assert "📏 整数股偏离：若本轮订单全部成交，SOXX.US 预计 0.0% vs 目标 15.0%（-15.0pp）" in message


def test_render_cycle_summary_includes_small_account_bootstrap_note_zh():
    message = render_cycle_summary(
        {
            "account": "****1234",
            "strategy_profile": "soxl_soxx_trend_income",
            "strategy_display_name": "SOXL/SOXX 半导体趋势收益",
            "dry_run_only": False,
            "portfolio": {
                "total_equity": 623.39,
                "liquid_cash": 623.39,
                "portfolio_rows": (("SOXL", "SOXX"), ("BOXX",)),
                "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0},
                "quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 0},
            },
            "allocation": {
                "targets": {"SOXL": 233.18, "SOXX": 0.0, "BOXX": 0.0},
                "small_account_whole_share_bootstrap_symbols": ("SOXL",),
            },
            "execution": {
                "reserved_cash": 150.0,
                "investable_cash": 473.39,
                "signal_date": "2026-06-23",
                "effective_date": "2026-06-24",
                "execution_timing_contract": "next_trading_day",
            },
            "submitted_orders": [],
            "skipped_orders": [],
        },
        lang="zh",
    )

    assert "ℹ️ [买入说明] SOXL.US 目标金额接近 1 股" in message
    assert "小账户整数股兼容，本轮允许按 1 股下单" in message


def test_render_cycle_summary_formats_skipped_orders_in_unified_english_template():
    message = render_cycle_summary(
        {
            "account": "****1234",
            "strategy_profile": "tqqq_growth_income",
            "strategy_display_name": "TQQQ Growth Income",
            "dry_run_only": False,
            "portfolio": {
                "total_equity": 98.65,
                "liquid_cash": 98.65,
                "portfolio_rows": (("TQQQ", "QQQ"), ("BOXX",)),
                "market_values": {"TQQQ": 0.0, "QQQ": 0.0, "BOXX": 0.0},
                "quantities": {"TQQQ": 0, "QQQ": 0, "BOXX": 0},
            },
            "allocation": {"targets": {"TQQQ": 44.39, "QQQ": 44.39, "BOXX": 7.89}},
            "execution": {
                "reserved_cash": 1.97,
                "investable_cash": 96.68,
                "signal_display": "entry",
                "signal_date": "2026-05-20",
                "effective_date": "2026-05-21",
                "execution_timing_contract": "next_trading_day",
            },
            "submitted_orders": [],
            "skipped_orders": [
                {"symbol": "TQQQ", "reason": "buy_quantity_zero"},
                {"symbol": "QQQ", "reason": "buy_quantity_zero"},
                {"symbol": "BOXX", "reason": "below_trade_threshold"},
            ],
        },
        lang="en",
    )

    assert "🔔 【Rebalance Instruction】" in message
    assert "🆔 Account: ****1234" in message
    assert "📌 Strategy Account" in message
    assert "⏱ Timing: 2026-05-20 -> 2026-05-21 (next trading day)" in message
    assert "🎯 Signal: Entry Signal" in message
    assert "Target changes: BOXX +7.89 USD, QQQ +44.39 USD, TQQQ +44.39 USD" in message
    assert "🧾 Execution details" in message
    assert (
        "No order submitted: reason=TQQQ,QQQ (whole-share quantity rounds to 0; no order needed), "
        "BOXX (below trade threshold)"
    ) in message
    assert "账户" not in message
    assert "信号" not in message
    assert "profile:" not in message
    assert "targets:" not in message


def test_render_cycle_summary_shows_funding_blocked_banner():
    message = render_cycle_summary(
        {
            "account": "****1234",
            "strategy_profile": "russell_top50_leader_rotation",
            "strategy_display_name": "Russell Top50 Leader Rotation",
            "dry_run_only": False,
            "execution_blocked": True,
            "execution_block_retryable": False,
            "funding_blocked": True,
            "execution_blocking_skips": [
                {"symbol": "NVDA", "reason": "insufficient_cash_for_whole_share"}
            ],
            "portfolio": {
                "total_equity": 50.0,
                "liquid_cash": 50.0,
                "portfolio_rows": (("NVDA",),),
                "market_values": {"NVDA": 0.0},
                "quantities": {"NVDA": 0},
            },
            "allocation": {"targets": {"NVDA": 500.0}},
            "execution": {},
            "submitted_orders": [],
            "skipped_orders": [
                {"symbol": "NVDA", "reason": "insufficient_cash_for_whole_share"}
            ],
        },
        lang="zh",
    )

    assert "⚠️ 资金不足，本周期不再自动重试: NVDA（现金不足以买入一整股）" in message


def test_render_cycle_summary_shows_retryable_execution_blocked_banner():
    message = render_cycle_summary(
        {
            "account": "****1234",
            "strategy_profile": "russell_top50_leader_rotation",
            "strategy_display_name": "Russell Top50 Leader Rotation",
            "dry_run_only": False,
            "execution_blocked": True,
            "execution_block_retryable": True,
            "execution_blocking_skips": [
                {"symbol": "NVDA", "reason": "quote_unavailable"}
            ],
            "portfolio": {
                "total_equity": 500.0,
                "liquid_cash": 500.0,
                "portfolio_rows": (("NVDA",),),
                "market_values": {"NVDA": 0.0},
                "quantities": {"NVDA": 0},
            },
            "allocation": {"targets": {"NVDA": 500.0}},
            "execution": {},
            "submitted_orders": [],
            "skipped_orders": [
                {"symbol": "NVDA", "reason": "quote_unavailable"}
            ],
        },
        lang="en",
    )

    assert "⚠️ Execution blocked; retryable within window: NVDA (quote unavailable)" in message
