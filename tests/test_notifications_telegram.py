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
    assert "  - Available cash: $456.78" in message
    assert "  - Reserved cash: $50.00" in message
    assert "  - Investable cash: $406.78" in message
    assert "  - SOXL: $1,000.00 / 5 shares" in message
    assert "Market Regime Control: watch | route none | score n/a" in message
    assert "Total assets (strategy symbols + cash): $2,000.00" not in message
    assert "Buying power: $100.00" not in message
    assert "📌 Strategy portfolio" not in message
    assert "🎯 Signal:" not in message


def test_render_cycle_summary_includes_tqqq_volatility_delever_risk_control():
    message = render_cycle_summary(
        {
            "account": "****1234",
            "strategy_profile": "tqqq_growth_income",
            "strategy_display_name": "TQQQ Growth Income",
            "dry_run_only": False,
            "portfolio": {
                "total_equity": 10000.0,
                "liquid_cash": 1000.0,
                "portfolio_rows": (("TQQQ", "QQQM"), ("BOXX", "QQQI")),
                "market_values": {"TQQQ": 0.0, "QQQM": 7000.0, "BOXX": 2000.0, "QQQI": 0.0},
                "quantities": {"TQQQ": 0, "QQQM": 60, "BOXX": 20, "QQQI": 0},
            },
            "allocation": {"targets": {"QQQM": 7000.0, "BOXX": 2000.0, "QQQI": 0.0}},
            "execution": {
                "reserved_cash": 1000.0,
                "investable_cash": 0.0,
                "signal_display": "Entry signal",
                "status_display": "Entry signal",
                "dual_drive_volatility_delever_applied": True,
                "dual_drive_volatility_delever_window": 5,
                "dual_drive_volatility_delever_metric": 0.312,
                "dual_drive_volatility_delever_threshold": 0.28,
                "dual_drive_volatility_delever_threshold_mode": "rolling_percentile",
                "dual_drive_volatility_delever_dynamic_threshold": 0.30,
                "dual_drive_volatility_delever_dynamic_sample_count": 252,
                "dual_drive_volatility_delever_dynamic_lookback": 252,
                "dual_drive_volatility_delever_dynamic_percentile": 0.90,
                "dual_drive_volatility_delever_dynamic_min_periods": 126,
                "dual_drive_volatility_delever_dynamic_floor": 0.24,
                "dual_drive_volatility_delever_dynamic_cap": 0.36,
                "dual_drive_volatility_delever_redirect_symbol": "QQQM",
                "dual_drive_volatility_delever_retained_ratio": 0.0,
                "dual_drive_volatility_delever_redirected_ratio": 1.0,
            },
            "submitted_orders": [],
            "skipped_orders": [],
        },
        lang="en",
    )

    assert "🛡️ Risk control:" not in message


def test_render_cycle_summary_relabels_total_assets_when_margin_is_enabled():
    message = render_cycle_summary(
        {
            "account": "****1234",
            "strategy_profile": "tqqq_growth_income",
            "dry_run_only": False,
            "portfolio": {
                "total_equity": 50000.0,
                "liquid_cash": 75000.0,
                "portfolio_rows": (("TQQQ",),),
                "market_values": {"TQQQ": 8000.0},
                "quantities": {"TQQQ": 10},
            },
            "allocation": {"targets": {"TQQQ": 8000.0}},
            "execution": {"cash_only_execution": False},
            "submitted_orders": [],
            "skipped_orders": [],
        },
        lang="zh",
    )

    assert "总资产（策略净值）: $50,000.00" in message
    assert "购买力: $75,000.00" in message
    assert "不含融资额度" not in message
