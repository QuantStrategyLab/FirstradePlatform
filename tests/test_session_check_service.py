from __future__ import annotations

from datetime import datetime, timezone

from application.firstrade_client import FirstradeCredentials
from application.session_check_service import build_account_funds_snapshot, run_session_check


class FakeClient:
    def __init__(self, _credentials, *, live_trading_enabled=False):
        self.live_trading_enabled = live_trading_enabled
        self.session_reused = True

    def connect(self):
        return self

    def select_account(self, requested_account=None):
        return requested_account or "12345678"

    def list_account_summaries(self):
        return [{"account": "****5678", "total_value": "100.00"}]

    def get_balances(self, _account):
        return {
            "result": {
                "total_account_value": "100.00",
                "cash_balance": "40.00",
                "margin_buying_power": "80.00",
                "unrelated": "ignored",
            }
        }

    def get_positions(self, _account):
        return {
            "items": [
                {"symbol": "SPY", "quantity": "2", "market_value": "900.50"},
                {"ticker": "QQQ", "qty": "1", "value": "450.25"},
            ]
        }


class FakeStateStore:
    def __init__(self):
        self.writes = []

    def write_json(self, key, payload):
        self.writes.append((key, payload))
        return True


def test_build_account_funds_snapshot_masks_account_and_compacts_values():
    snapshot = build_account_funds_snapshot(
        account="12345678",
        account_summaries=[{"account": "****5678", "total_value": "100.00"}],
        balances={"total_account_value": "100.00", "cash_balance": "40.00", "note": "x"},
        positions_payload={"items": [{"symbol": "SPY", "quantity": "2", "market_value": "900.50"}]},
        session_reused=True,
        now=datetime(2026, 5, 23, 1, 2, 3, tzinfo=timezone.utc),
    )

    assert snapshot["account"] == "****5678"
    assert snapshot["session_reused"] is True
    assert snapshot["balance_metrics"] == {
        "total_account_value": 100.0,
        "cash_balance": 40.0,
    }
    assert snapshot["positions"] == [
        {"symbol": "SPY", "quantity": 2.0, "market_value": 900.5}
    ]


def test_run_session_check_persists_funds_snapshot_when_enabled():
    store = FakeStateStore()
    now = datetime(2026, 5, 23, 1, 2, 3, tzinfo=timezone.utc)

    result = run_session_check(
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FakeClient,
        state_store=store,
        env_reader=lambda name, default=None: {
            "FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT": "true",
            "FIRSTRADE_SESSION_CHECK_INCLUDE_POSITIONS": "true",
        }.get(name, default),
        now=now,
    )

    assert result["ok"] is True
    assert result["session_reused"] is True
    assert result["snapshot_persisted"] is True
    assert len(store.writes) == 2
    assert store.writes[0][0] == "accounts/____5678/funds/latest.json"
    assert store.writes[1][0] == "accounts/____5678/funds/history/2026/05/23/20260523T010203Z.json"
    assert store.writes[0][1]["positions"][0]["symbol"] == "SPY"
