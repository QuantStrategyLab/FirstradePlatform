from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from application.paper_execution_command_consumer import (
    FIRSTRADE_PAPER_EXECUTION_INTENT_SCHEMA_VERSION,
    consume_due_paper_execution_commands,
    resolve_paper_execution_command_consumer_enabled,
)
from quant_platform_kit.common.execution_commands import ExecutionCommand, ExecutionCommandState, ExecutionCommandStore
from quant_platform_kit.common.models import PortfolioSnapshot, Position, QuoteSnapshot
from quant_platform_kit.common.paper_execution_admission import (
    PAPER_RISK_ADMISSION_RECEIPT_INTENT_FIELD,
    build_paper_risk_admission_receipt,
)
from quant_platform_kit.common.strategy_release import build_runtime_loaded_receipt


def _release() -> dict[str, str]:
    return {
        "release_id": "tqqq-p3-v6.20260824",
        "manifest_sha256": "a" * 64,
        "strategy_revision": "tqqq-p3-v6",
        "config_sha256": "b" * 64,
        "risk_policy_sha256": "c" * 64,
        "evidence_sha256": "d" * 64,
        "plugin_bundle_sha256": "e" * 64,
        "effective_session": "2026-08-25",
    }


def _command(*, platform: str = "firstrade") -> ExecutionCommand:
    release = _release()
    intent = {
        "schema_version": FIRSTRADE_PAPER_EXECUTION_INTENT_SCHEMA_VERSION,
        "target_mode": "value",
        "targets": {"TQQQ": 300.0, "BOXX": 100.0},
        "strategy_symbols": ["TQQQ", "BOXX"],
        "strategy_release": release,
    }
    decision_digest = hashlib.sha256(
        json.dumps(intent, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    intent[PAPER_RISK_ADMISSION_RECEIPT_INTENT_FIELD] = build_paper_risk_admission_receipt(
        strategy_profile="tqqq_growth_income",
        release_id=release["release_id"],
        risk_policy_sha256=release["risk_policy_sha256"],
        decision_digest=decision_digest,
        effective_session="2026-08-25",
        disposition="allow_new_risk",
        reason_codes=(),
    ).to_dict()
    return ExecutionCommand.from_decision(
        platform=platform,
        account_scope="us",
        strategy_profile="tqqq_growth_income",
        execution_mode="paper",
        signal_date="2026-08-24",
        effective_date="2026-08-25",
        execution_timing_contract="next_trading_day",
        decision_digest=decision_digest,
        intent=intent,
    )


def _portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        as_of=datetime(2026, 8, 25, tzinfo=timezone.utc),
        total_equity=1_000.0,
        cash_balance=800.0,
        buying_power=800.0,
        positions=(
            Position(
                symbol="TQQQ",
                quantity=20.0,
                market_value=200.0,
                average_cost=8.0,
                currency="USD",
            ),
        ),
        metadata={"market_currency_cash": 800.0},
    )


def _quote(symbol: str) -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol,
        as_of=datetime(2026, 8, 25, tzinfo=timezone.utc),
        last_price=10.0,
    )


def _binding() -> dict[str, str]:
    return {
        "platform": "firstrade",
        "account_scope": "us",
        "strategy_profile": "tqqq_growth_income",
    }


def test_consumer_fills_reconciled_paper_command_without_order_client(tmp_path: Path) -> None:
    store = ExecutionCommandStore(local_dir=tmp_path)
    command = _command()
    assert store.enqueue(command)

    result = consume_due_paper_execution_commands(
        store=store,
        as_of_session="2026-08-25",
        claimant="firstrade-paper-command-consumer",
        portfolio_loader=_portfolio,
        quote_loader=_quote,
        managed_symbols=("TQQQ", "BOXX"),
        runtime_release_receipt=build_runtime_loaded_receipt(strategy_release=_release()),
        expected_strategy_release=_release(),
        expected_command_binding=_binding(),
    )

    assert result["status"] == "ok"
    assert result["commands"][0]["status"] == "filled"
    assert store.current_state(command) is ExecutionCommandState.FILLED
    proposals = store.events(command)[1].details["proposals"]
    assert [proposal["exposure_effect"] for proposal in proposals] == ["increases", "increases"]
    assert all("order" not in proposal["details"] for proposal in proposals)


def test_consumer_rejects_cross_platform_command_before_account_reads(tmp_path: Path) -> None:
    store = ExecutionCommandStore(local_dir=tmp_path)
    command = _command(platform="schwab")
    assert store.enqueue(command)
    reads = {"portfolio": 0, "quote": 0}

    def portfolio_loader():
        reads["portfolio"] += 1
        return _portfolio()

    def quote_loader(symbol: str):
        reads["quote"] += 1
        return _quote(symbol)

    result = consume_due_paper_execution_commands(
        store=store,
        as_of_session="2026-08-25",
        claimant="firstrade-paper-command-consumer",
        portfolio_loader=portfolio_loader,
        quote_loader=quote_loader,
        managed_symbols=("TQQQ", "BOXX"),
        runtime_release_receipt=build_runtime_loaded_receipt(strategy_release=_release()),
        expected_strategy_release=_release(),
        expected_command_binding=_binding(),
    )

    assert result["commands"][0]["status"] == "rejected"
    assert reads == {"portfolio": 0, "quote": 0}
    assert store.current_state(command) is ExecutionCommandState.REJECTED


def test_consumer_flag_cannot_be_enabled_outside_dry_run() -> None:
    with pytest.raises(RuntimeError, match="FIRSTRADE_DRY_RUN_ONLY=true"):
        resolve_paper_execution_command_consumer_enabled(
            env_reader=lambda *_args: "true",
            dry_run_only=False,
        )
