from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from application.broker_reconciliation import (
    FirstradeReconciliationUnavailable,
    FirstradeReconciliationObservations,
    build_reconciliation_candidate,
    collect_read_only_reconciliation_observations,
    collect_broker_reconciliation_evidence,
    validate_reconciliation_candidate,
)
from quant_platform_kit.common.broker_reconciliation import (
    build_broker_reconciliation_evidence,
    calculate_broker_observation_sha256,
)


def _evidence():
    return build_broker_reconciliation_evidence(
        platform_id="firstrade",
        strategy_profile="sample_profile",
        account_scope_sha256="1" * 64,
        baseline_id="firstrade-baseline-001",
        baseline_target_sha256="2" * 64,
        runtime_target_sha256="2" * 64,
        observed_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        broker_connected=True,
        account_identity_match=True,
        positions_match=True,
        cash_match=True,
        open_orders_match=True,
        recent_executions_match=True,
        local_execution_ledger_match=True,
        positions_sha256="3" * 64,
        cash_sha256="4" * 64,
        open_orders_sha256="5" * 64,
        recent_executions_sha256="6" * 64,
        local_execution_ledger_sha256="7" * 64,
    )


def test_reconciliation_entrypoint_fails_closed_before_collector_is_invoked():
    calls = 0

    def collector():
        nonlocal calls
        calls += 1
        return _evidence()

    with pytest.raises(FirstradeReconciliationUnavailable, match="not configured"):
        collect_broker_reconciliation_evidence()

    assert calls == 0


def test_reconciliation_entrypoint_returns_only_qpk_evidence():
    evidence = _evidence()

    assert collect_broker_reconciliation_evidence(collector=lambda: evidence) is evidence


def test_reconciliation_entrypoint_rejects_non_qpk_collector_result():
    with pytest.raises(FirstradeReconciliationUnavailable, match="QPK evidence"):
        collect_broker_reconciliation_evidence(collector=lambda: object())


def _runtime_target():
    return SimpleNamespace(
        platform_id="firstrade",
        strategy_profile="sample_profile",
        account_scope="US",
        live_continuity=SimpleNamespace(
            state="RECONCILE_ONLY",
            baseline_id="firstrade-baseline-001",
            baseline_target_sha256="2" * 64,
        ),
    )


class _ReadOnlyClient:
    def account_numbers(self):
        return ["account-sensitive-001"]

    def select_account(self, requested_account=None):
        assert requested_account == "account-sensitive-001"
        return requested_account

    def get_balances(self, account):
        assert account == "account-sensitive-001"
        return {"cash_balance": "100.25"}

    def get_positions(self, account):
        assert account == "account-sensitive-001"
        return {"items": [{"symbol": "SPY", "quantity": "1"}]}

    def get_orders(self, account, *, per_page=0):
        assert account == "account-sensitive-001"
        assert per_page == 0
        return [{"order_id": "order-sensitive-001", "status": "WORKING", "symbol": "SPY"}]


def test_read_only_observations_use_only_client_read_surfaces_and_mark_executions_unavailable():
    observations = collect_read_only_reconciliation_observations(
        _ReadOnlyClient(), requested_account="account-sensitive-001"
    )

    assert observations.account_identity_match is True
    assert observations.recent_executions_available is False
    assert observations.open_orders


@pytest.mark.parametrize(
    ("method_name", "value"),
    [
        ("get_balances", {}),
        ("get_positions", []),
        ("get_orders", [{"order_id": "missing-status"}]),
    ],
)
def test_read_only_observations_reject_incomplete_surfaces(method_name, value):
    class IncompleteClient(_ReadOnlyClient):
        pass

    setattr(IncompleteClient, method_name, lambda self, *_args, **_kwargs: value)

    with pytest.raises(FirstradeReconciliationUnavailable):
        collect_read_only_reconciliation_observations(
            IncompleteClient(), requested_account="account-sensitive-001"
        )


def test_candidate_with_no_immutable_baseline_is_redacted_and_remains_blocked():
    observations = FirstradeReconciliationObservations(
        account_scope={"account_id": "account-sensitive-001"},
        account_identity_match=True,
        positions={"items": [{"symbol": "SPY", "quantity": "1"}]},
        cash={"cash_balance": "100.25"},
        open_orders=({"order_id": "order-sensitive-001", "status": "WORKING"},),
        recent_executions={"availability": "unavailable"},
        recent_executions_available=False,
    )

    candidate = build_reconciliation_candidate(
        observations=observations,
        runtime_target=_runtime_target(),
        project_id=None,
        ledger_digest_reader=lambda: ("7" * 64, 0),
        observed_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
        env_reader=lambda _name, _default=None: None,
    )
    payload = candidate.to_safe_dict()

    assert payload["permits_active_lkg"] is False
    assert payload["expected_digests_configured"] is False
    assert "broker_reconciliation_recent_executions_mismatch" in payload["recovery_blockers"]
    serialized = str(payload)
    for raw in ("account-sensitive-001", "order-sensitive-001", "100.25"):
        assert raw not in serialized


def _observations_with_positions(monkeypatch, positions):
    monkeypatch.setattr(_ReadOnlyClient, "get_positions", lambda *_: positions)
    return collect_read_only_reconciliation_observations(
        _ReadOnlyClient(), requested_account="account-sensitive-001"
    )


def _candidate(observations, expected_positions=None):
    # Synthetic expected accounting facts, never a production baseline rewrite.
    expected = {
        "account_scope_sha256": "1" * 64,
        "positions_sha256": calculate_broker_observation_sha256(
            expected_positions if expected_positions is not None else [{"symbol": "SPY", "quantity": "1"}]
        ),
        "cash_sha256": "3" * 64,
        "open_orders_sha256": "4" * 64,
        "recent_executions_sha256": "5" * 64,
        "local_execution_ledger_sha256": "7" * 64,
    }
    return build_reconciliation_candidate(
        observations=observations,
        runtime_target=_runtime_target(),
        project_id=None,
        ledger_digest_reader=lambda: ("7" * 64, 0),
        observed_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        env_reader=lambda *_: json.dumps(expected),
    )


def test_valuation_changes_leave_position_accounting_equal_but_keep_raw_data(monkeypatch):
    raw = {"items": [{"symbol": "SPY", "quantity": "1", "price": "500", "market_value": "500", "profit_loss": "10"}]}
    observations = _observations_with_positions(monkeypatch, raw)
    first = _candidate(observations)
    changed = {"items": [{**raw["items"][0], "price": "501", "market_value": "501", "profit_loss": "11"}]}
    current = _observations_with_positions(monkeypatch, changed)
    second = _candidate(current)

    assert first.evidence.positions_match and second.evidence.positions_match
    assert first.evidence.positions_sha256 == second.evidence.positions_sha256
    assert observations.positions == raw and current.positions == changed
    assert validate_reconciliation_candidate(second) == second.to_safe_dict()
    assert not second.evidence.cash_match
    assert not second.recent_executions_available and not second.permits_active_lkg


@pytest.mark.parametrize("row", [
    {"symbol": "IBIT", "quantity": "1"},
    {"symbol": "SPY", "quantity": "1.00000000000000000000000000001"},
    {"symbol": "SPY", "quantity": "0"},
])
def test_symbol_or_exact_quantity_changes_are_detected(monkeypatch, row):
    observations = _observations_with_positions(monkeypatch, {"items": [row]})
    assert not _candidate(observations).evidence.positions_match


def test_position_projection_keeps_unmanaged_holdings_and_ignores_row_order(monkeypatch):
    expected = [{"symbol": "IBIT", "quantity": "2"}, {"symbol": "SPY", "quantity": "1"}]
    observations = _observations_with_positions(monkeypatch, {"items": list(reversed(expected))})
    assert _candidate(observations, expected).evidence.positions_match
    assert not _candidate(observations, [expected[1]]).evidence.positions_match


@pytest.mark.parametrize("quantity", [1, 1.0, "1.000", "1e0"])
def test_quantity_formatting_is_exactly_normalized_without_rounding(monkeypatch, quantity):
    observations = _observations_with_positions(monkeypatch, {"items": [{"symbol": "SPY", "quantity": quantity}]})
    assert _candidate(observations).evidence.positions_match


@pytest.mark.parametrize("symbol", ["spy", " SPY "])
def test_position_symbol_formatting_is_normalized(monkeypatch, symbol):
    observations = _observations_with_positions(monkeypatch, {"items": [{"symbol": symbol, "quantity": "1"}]})
    assert _candidate(observations).evidence.positions_match


def test_explicit_empty_positions_are_valid_without_proving_recovery(monkeypatch):
    observations = _observations_with_positions(monkeypatch, {"items": []})
    candidate = _candidate(observations, [])
    assert candidate.evidence.positions_match
    assert not candidate.permits_active_lkg


@pytest.mark.parametrize("surface", ["collector", "candidate"])
@pytest.mark.parametrize("positions", [
    {}, [], {"positions": []}, {"items": None}, {"items": {}}, {"items": [None]},
    {"error": "synthetic-denial", "items": []},
    {"items": [{"symbol": "SPY"}]},
    {"items": [{"quantity": "1"}]},
    {"items": [{"symbol": "", "quantity": "1"}]},
    {"items": [{"symbol": 1, "quantity": "1"}]},
    {"items": [{"symbol": "SPY", "quantity": "1"}, {"symbol": "SPY", "quantity": "2"}]},
    {"items": [{"symbol": "SPY", "quantity": "1"}, {"symbol": " spy ", "quantity": "2"}]},
    *({"items": [{"symbol": "SPY", "quantity": value}]} for value in [None, True, {}, "", "NaN", "Infinity", float("nan"), float("inf")]),
])
def test_invalid_position_facts_are_never_empty_or_candidate_evidence(monkeypatch, surface, positions):
    if surface == "candidate":
        observations = _observations_with_positions(monkeypatch, {"items": []})
        observations = replace(observations, positions=positions)
    with pytest.raises(FirstradeReconciliationUnavailable):
        if surface == "collector":
            _observations_with_positions(monkeypatch, positions)
        else:
            _candidate(observations)


def test_legacy_full_payload_digest_is_not_reinterpreted_as_accounting_baseline(monkeypatch):
    raw = {"items": [{"symbol": "SPY", "quantity": "1"}]}
    observations = _observations_with_positions(monkeypatch, raw)
    assert not _candidate(observations, raw).evidence.positions_match
