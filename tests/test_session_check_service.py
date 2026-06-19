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
    def __init__(self, reads=None):
        self.payloads = dict(reads or {})
        self.reads = []
        self.writes = []

    def read_json(self, key):
        self.reads.append(key)
        return self.payloads.get(key)

    def write_json(self, key, payload):
        self.writes.append((key, payload))
        self.payloads[key] = payload
        return True


class ExplodingClient:
    def __init__(self, *_args, **_kwargs):
        raise AssertionError("client should not be created when session-check is skipped")


def _env(values):
    return lambda name, default=None: values.get(name, default)


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


def test_monthly_session_check_skips_when_current_period_is_already_maintained():
    now = datetime(2026, 6, 3, 1, 2, 3, tzinfo=timezone.utc)
    state_key = (
        "session-checks/auto/russell_top50_leader_rotation/2026_06/latest.json"
    )
    store = FakeStateStore(
        {
            state_key: {
                "checked_at": "2026-06-01T01:02:03+00:00",
                "period": "2026-06",
            }
        }
    )

    result = run_session_check(
        client_factory=ExplodingClient,
        state_store=store,
        env_reader=_env({"STRATEGY_PROFILE": "russell_top50_leader_rotation"}),
        now=now,
    )

    assert result["ok"] is True
    assert result["session_check_skipped"] is True
    assert result["session_check_policy"] == "auto"
    assert result["session_check_period"] == "2026-06"
    assert result["session_check_last_checked_at"] == "2026-06-01T01:02:03+00:00"
    assert store.reads == [state_key]
    assert store.writes == []


def test_monthly_session_check_runs_and_persists_maintenance_state_when_due():
    now = datetime(2026, 6, 3, 1, 2, 3, tzinfo=timezone.utc)
    store = FakeStateStore()

    result = run_session_check(
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FakeClient,
        state_store=store,
        env_reader=_env({"STRATEGY_PROFILE": "russell_top50_leader_rotation"}),
        now=now,
    )

    assert result["ok"] is True
    assert result["session_check_maintenance_state_persisted"] is True
    state_key = (
        "session-checks/auto/russell_top50_leader_rotation/2026_06/latest.json"
    )
    assert store.reads == [state_key]
    assert store.writes == [
        (
            state_key,
            {
                "checked_at": "2026-06-03T01:02:03+00:00",
                "account": "****5678",
                "session_reused": True,
                "strategy_profile": "russell_top50_leader_rotation",
                "strategy_cadence": "monthly",
                "strategy_required_inputs": ["feature_snapshot"],
                "period": "2026-06",
                "policy": "auto",
            },
        )
    ]


def test_daily_session_check_runs_every_time_without_maintenance_state_lookup():
    now = datetime(2026, 6, 3, 1, 2, 3, tzinfo=timezone.utc)
    store = FakeStateStore()

    result = run_session_check(
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FakeClient,
        state_store=store,
        env_reader=_env({"STRATEGY_PROFILE": "tqqq_growth_income"}),
        now=now,
    )

    assert result["ok"] is True
    assert result["session_check_policy_reason"] == "daily_strategy"
    assert result["session_check_maintenance_state_persisted"] is False
    assert store.reads == []
    assert store.writes == []


def test_session_check_policy_always_overrides_monthly_throttle():
    now = datetime(2026, 6, 3, 1, 2, 3, tzinfo=timezone.utc)
    state_key = (
        "session-checks/auto/russell_top50_leader_rotation/2026_06/latest.json"
    )
    store = FakeStateStore({state_key: {"checked_at": "2026-06-01T01:02:03+00:00"}})

    result = run_session_check(
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FakeClient,
        state_store=store,
        env_reader=_env(
            {
                "STRATEGY_PROFILE": "russell_top50_leader_rotation",
                "FIRSTRADE_SESSION_CHECK_POLICY": "always",
            }
        ),
        now=now,
    )

    assert result["ok"] is True
    assert result["session_check_policy"] == "always"
    assert result["session_check_policy_reason"] == "policy_always"
    assert result["session_check_maintenance_state_persisted"] is False
    assert store.reads == []
    assert store.writes == []


def test_session_check_policy_skip_does_not_require_credentials_or_client():
    result = run_session_check(
        client_factory=ExplodingClient,
        env_reader=_env({"FIRSTRADE_SESSION_CHECK_POLICY": "skip"}),
        now=datetime(2026, 6, 3, 1, 2, 3, tzinfo=timezone.utc),
    )

    assert result["ok"] is True
    assert result["session_check_skipped"] is True
    assert result["session_check_policy"] == "skip"
    assert result["session_check_policy_reason"] == "policy_skip"
