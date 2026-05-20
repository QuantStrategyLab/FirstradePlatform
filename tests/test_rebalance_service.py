from __future__ import annotations

from types import SimpleNamespace

from application.firstrade_client import FirstradeCredentials
from application.rebalance_service import run_strategy_cycle
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
    assert "Firstrade Strategy Cycle" in messages[0]
    assert "buy AAA x2.0" in messages[0]
