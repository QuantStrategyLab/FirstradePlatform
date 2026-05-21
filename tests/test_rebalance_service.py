from __future__ import annotations

from types import SimpleNamespace

from application.firstrade_client import FirstradeCredentials
from application.rebalance_service import run_strategy_cycle
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


def test_notification_i18n_keys_are_aligned():
    assert set(I18N["zh"]) == set(I18N["en"])
    assert build_translator("zh")("account_label", account="****1234") == "🆔 账户: ****1234"
    assert build_translator("en")("account_label", account="****1234") == "🆔 Account: ****1234"


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
    assert "🧪 Dry-run buy: AAA 2 shares" in messages[0]


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
    assert "未下单: 原因=买入股数为0:TQQQ,QQQ, 低于调仓阈值:BOXX" in message
    assert "profile:" not in message
    assert "targets:" not in message


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
    assert "No order submitted: reason=buy quantity rounds to 0:TQQQ,QQQ, below trade threshold:BOXX" in message
    assert "账户" not in message
    assert "信号" not in message
    assert "profile:" not in message
    assert "targets:" not in message
