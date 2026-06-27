from __future__ import annotations

from datetime import datetime, timezone

from application.runtime_broker_adapters import build_runtime_broker_adapters


def _timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class FakeClient:
    def __init__(self, *, quote_payload=None, quote_error: Exception | None = None, ohlc=None):
        self.quote_payload = quote_payload or {"last": "10.50", "bid": "10.40", "ask": "10.60"}
        self.quote_error = quote_error
        self.ohlc = ohlc or [(1700000000000, 9, 11, 8, 10, 1000)]

    def get_quote(self, _account, symbol):
        if self.quote_error is not None:
            raise self.quote_error
        return {"symbol": symbol, **self.quote_payload}

    def get_ohlc(self, _symbol, _range):
        return self.ohlc

    def get_balances(self, _account):
        return {"total_value": "120.00", "cash": "20.00", "buying_power": "30.00"}

    def get_positions(self, _account):
        return {"items": [{"symbol": "SPY", "quantity": "2", "market_value": "21.00"}]}

    def place_stock_order(self, request, dry_run=True):
        return {
            "symbol": request.symbol,
            "dry_run": dry_run,
            "notional": request.notional_usd is not None,
            "quantity": request.quantity,
            "notional_usd": request.notional_usd,
        }


def test_runtime_adapters_build_quote_and_portfolio_ports():
    adapters = build_runtime_broker_adapters(
        client=FakeClient(),
        account="12345678",
        strategy_symbols=("SPY",),
    )
    quote = adapters.build_market_data_port().get_quote("SPY")
    portfolio = adapters.build_portfolio_port().get_portfolio_snapshot()

    assert quote.last_price == 10.5
    assert portfolio.total_equity == 41.0
    assert portfolio.cash_balance == 20.0
    assert portfolio.positions[0].symbol == "SPY"
    assert portfolio.metadata["total_equity_source"] == "cash_plus_positions"


def test_portfolio_snapshot_uses_account_value_balance_key():
    class AccountValueClient(FakeClient):
        def get_balances(self, _account):
            return {"account_value": "$1,234.56", "cash_balance": "$200.00"}

    adapters = build_runtime_broker_adapters(
        client=AccountValueClient(),
        account="12345678",
    )

    portfolio = adapters.build_portfolio_port().get_portfolio_snapshot()

    assert portfolio.total_equity == 221.0
    assert portfolio.cash_balance == 200.0
    assert portfolio.metadata["total_equity_source"] == "cash_plus_positions"


def test_managed_portfolio_snapshot_ignores_full_account_value_balance_key():
    class AccountValueClient(FakeClient):
        def get_balances(self, _account):
            return {"account_value": "$1,234.56", "cash_balance": "$200.00"}

        def get_positions(self, _account):
            return {
                "items": [
                    {"symbol": "SPY", "quantity": "2", "market_value": "21.00"},
                    {"symbol": "AAPL", "quantity": "3", "market_value": "300.00"},
                ]
            }

    adapters = build_runtime_broker_adapters(
        client=AccountValueClient(),
        account="12345678",
        strategy_symbols=("SPY",),
    )

    portfolio = adapters.build_portfolio_port().get_portfolio_snapshot()

    assert portfolio.total_equity == 221.0
    assert [position.symbol for position in portfolio.positions] == ["SPY"]
    assert portfolio.metadata["total_equity_source"] == "cash_plus_positions"


def test_portfolio_snapshot_falls_back_to_cash_when_total_value_missing():
    class CashOnlyClient(FakeClient):
        def get_balances(self, _account):
            return {"cash_balance": "$120.00", "buying_power": "$120.00"}

        def get_positions(self, _account):
            return {"items": []}

    adapters = build_runtime_broker_adapters(
        client=CashOnlyClient(),
        account="12345678",
        strategy_symbols=("SPY",),
    )

    portfolio = adapters.build_portfolio_port().get_portfolio_snapshot()

    assert portfolio.total_equity == 120.0
    assert portfolio.metadata["total_equity_source"] == "cash_plus_positions"


def test_price_series_appends_live_quote_when_history_lags_today():
    adapters = build_runtime_broker_adapters(
        client=FakeClient(
            quote_payload={"last": "12.00", "bid": "11.90", "ask": "12.10"},
            ohlc=[
                (
                    _timestamp_ms(datetime(2026, 5, 26, 4, tzinfo=timezone.utc)),
                    9,
                    11,
                    8,
                    10,
                    1000,
                )
            ],
        ),
        account="12345678",
        clock=lambda: datetime(2026, 5, 27, 19, 45, tzinfo=timezone.utc),
    )

    series = adapters.build_market_data_port().get_price_series("SPY")

    assert [point.close for point in series.points] == [10.0, 12.0]


def test_price_series_replaces_same_day_history_with_live_quote():
    adapters = build_runtime_broker_adapters(
        client=FakeClient(
            quote_payload={"last": "12.00", "bid": "11.90", "ask": "12.10"},
            ohlc=[
                (
                    _timestamp_ms(datetime(2026, 5, 27, 4, tzinfo=timezone.utc)),
                    9,
                    11,
                    8,
                    10,
                    1000,
                )
            ],
        ),
        account="12345678",
        clock=lambda: datetime(2026, 5, 27, 19, 45, tzinfo=timezone.utc),
    )

    series = adapters.build_market_data_port().get_price_series("SPY")

    assert [point.close for point in series.points] == [12.0]


def test_price_series_falls_back_to_history_when_quote_unavailable():
    adapters = build_runtime_broker_adapters(
        client=FakeClient(
            quote_error=RuntimeError("quote unavailable"),
            ohlc=[
                (
                    _timestamp_ms(datetime(2026, 5, 26, 4, tzinfo=timezone.utc)),
                    9,
                    11,
                    8,
                    10,
                    1000,
                )
            ],
        ),
        account="12345678",
        clock=lambda: datetime(2026, 5, 27, 19, 45, tzinfo=timezone.utc),
    )

    series = adapters.build_market_data_port().get_price_series("SPY")

    assert [point.close for point in series.points] == [10.0]


def test_execution_port_submits_notional_buy_from_metadata():
    captured = {}

    class CapturingClient(FakeClient):
        def place_stock_order(self, request, dry_run=True, explicit_live_ack=False):
            captured["request"] = request
            return super().place_stock_order(request, dry_run=dry_run)

    from quant_platform_kit.common.models import OrderIntent

    adapters = build_runtime_broker_adapters(
        client=CapturingClient(),
        account="12345678",
    )
    report = adapters.build_execution_port().submit_order(
        OrderIntent(
            symbol="QQQM",
            side="buy",
            quantity=0.0,
            order_type="market",
            metadata={"notional_usd": 50.0, "max_notional_usd": 100.0},
        )
    )

    request = captured["request"]
    assert request.notional_usd == 50.0
    assert request.quantity is None
    assert request.price_type == "market"
    assert request.max_notional_usd == 100.0
    assert report.quantity == 50.0
    assert report.status == "previewed"
