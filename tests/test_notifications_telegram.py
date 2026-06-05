from __future__ import annotations

from notifications.telegram import render_cycle_summary


def test_render_cycle_summary_dashboard_text_does_not_hide_account_overview():
    message = render_cycle_summary(
        {
            "account": "****1234",
            "strategy_profile": "soxl_soxx_trend_income",
            "strategy_display_name": "SOXL/SOXX Semiconductor Trend Income",
            "dry_run_only": True,
            "portfolio": {
                "total_equity": 2345.67,
                "liquid_cash": 456.78,
                "portfolio_rows": (("SOXL", "SOXX"), ("BOXX",)),
                "market_values": {"SOXL": 1000.0, "SOXX": 500.0, "BOXX": 0.0},
                "quantities": {"SOXL": 5, "SOXX": 2, "BOXX": 0},
            },
            "allocation": {"targets": {"SOXL": 1000.0, "SOXX": 500.0, "BOXX": 0.0}},
            "execution": {
                "reserved_cash": 50.0,
                "investable_cash": 406.78,
                "dashboard_text": "\n".join(
                    (
                        "📌 Strategy portfolio",
                        "  - Total assets (strategy symbols + cash): $2,000.00",
                        "  - Buying power: $100.00",
                        "💼 Strategy holdings",
                        "  - SOXL: $1,000.00 / 5 shares",
                        "🎯 Signal: signal_blend_gate_risk_on: soxl_ratio=70.0%, soxx_ratio=20.0%",
                        "Market Regime Control: watch | route none | score n/a",
                    )
                ),
                "signal_display": "signal_blend_gate_risk_on: soxl_ratio=70.0%, soxx_ratio=20.0%, trend_symbol=SOXX, window=140",
            },
            "submitted_orders": [],
            "skipped_orders": [],
        },
        lang="en",
    )

    assert "  - Total assets: $2,345.67" in message
    assert "  - Buying power: $456.78" in message
    assert "  - Reserved cash: $50.00" in message
    assert "  - Investable cash: $406.78" in message
    assert "  - SOXL: $1,000.00 / 5 shares" in message
    assert "Market Regime Control: watch | route none | score n/a" in message
    assert "Total assets (strategy symbols + cash): $2,000.00" not in message
    assert "Buying power: $100.00" not in message
    assert "📌 Strategy portfolio" not in message
    assert message.count("🎯 Signal:") == 1
