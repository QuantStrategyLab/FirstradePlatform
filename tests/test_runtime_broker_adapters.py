from __future__ import annotations

from application.runtime_broker_adapters import build_runtime_broker_adapters


class FakeClient:
    def get_quote(self, _account, symbol):
        return {"symbol": symbol, "last": "10.50", "bid": "10.40", "ask": "10.60"}

    def get_ohlc(self, _symbol, _range):
        return [(1700000000000, 9, 11, 8, 10, 1000)]

    def get_balances(self, _account):
        return {"total_value": "120.00", "cash": "20.00", "buying_power": "30.00"}

    def get_positions(self, _account):
        return {"items": [{"symbol": "SPY", "quantity": "2", "market_value": "21.00"}]}

    def place_stock_order(self, request, dry_run=True):
        return {"symbol": request.symbol, "dry_run": dry_run}


def test_runtime_adapters_build_quote_and_portfolio_ports():
    adapters = build_runtime_broker_adapters(
        client=FakeClient(),
        account="12345678",
        strategy_symbols=("SPY",),
    )
    quote = adapters.build_market_data_port().get_quote("SPY")
    portfolio = adapters.build_portfolio_port().get_portfolio_snapshot()

    assert quote.last_price == 10.5
    assert portfolio.total_equity == 120.0
    assert portfolio.cash_balance == 20.0
    assert portfolio.positions[0].symbol == "SPY"
