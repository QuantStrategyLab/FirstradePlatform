from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from quant_platform_kit.common.us_equity_sessions import Session

import main


def test_market_hours_gate_uses_regular_session_only(monkeypatch):
    captured: dict[str, object] = {}

    def fake_session_gate(now, *, enabled_sessions):
        captured["now"] = now
        captured["enabled_sessions"] = enabled_sessions
        return Session.REGULAR, True

    monkeypatch.setattr(main, "_force_strategy_run_env", lambda: False)
    monkeypatch.setattr(main, "session_gate", fake_session_gate)
    monkeypatch.setattr(main, "is_market_open_now", lambda **_: (True, None))

    skip, payload = main._should_skip_for_market_hours()

    assert skip is False
    assert payload is None
    assert captured["enabled_sessions"] == frozenset({Session.REGULAR})
    assert captured["now"].tzinfo is not None


def test_market_hours_gate_skips_when_session_policy_disallows(monkeypatch):
    monkeypatch.setattr(main, "_force_strategy_run_env", lambda: False)
    monkeypatch.setattr(main, "session_gate", lambda *_, **__: (Session.PRE_MARKET, False))
    monkeypatch.setattr(
        main,
        "is_market_open_now",
        lambda **_: (_ for _ in ()).throw(AssertionError("calendar must not be consulted")),
    )

    skip, payload = main._should_skip_for_market_hours()

    assert skip is True
    assert payload is not None
    assert payload["skip_reason"] == "market_closed"


def test_market_hours_gate_still_requires_calendar_open(monkeypatch):
    monkeypatch.setattr(main, "_force_strategy_run_env", lambda: False)
    monkeypatch.setattr(main, "session_gate", lambda *_, **__: (Session.REGULAR, True))
    monkeypatch.setattr(main, "is_market_open_now", lambda **_: (False, None))

    skip, payload = main._should_skip_for_market_hours()

    assert skip is True
    assert payload is not None
    assert payload["skip_reason"] == "market_closed"


def test_market_hours_gate_force_env_bypasses_session_policy(monkeypatch):
    monkeypatch.setattr(main, "_force_strategy_run_env", lambda: True)
    monkeypatch.setattr(
        main,
        "session_gate",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("force env must bypass")),
    )

    skip, payload = main._should_skip_for_market_hours()

    assert skip is False
    assert payload is None


def _fixed_datetime(et_time):
    class FixedDatetime:
        @classmethod
        def now(cls, tz):
            return datetime.combine(
                datetime(2026, 10, 22).date(),
                et_time,
                tzinfo=ZoneInfo("America/New_York"),
            ).astimezone(tz)

    return FixedDatetime


@pytest.mark.parametrize(
    ("et_time", "expected_skip"),
    [
        ((9, 29), True),
        ((9, 30), False),
        ((12, 0), False),
        ((16, 0), True),
        ((3, 0), True),
    ],
)
def test_market_hours_gate_uses_real_qpk_session_windows(monkeypatch, et_time, expected_skip):
    from datetime import time as dt_time

    monkeypatch.setattr(main, "_force_strategy_run_env", lambda: False)
    monkeypatch.setattr(main, "datetime", _fixed_datetime(dt_time(*et_time)))
    monkeypatch.setattr(main, "is_market_open_now", lambda **_: (True, None))

    skip, _ = main._should_skip_for_market_hours()

    assert skip is expected_skip


def test_market_hours_gate_fails_closed_on_session_error(monkeypatch):
    monkeypatch.setattr(main, "_force_strategy_run_env", lambda: False)
    monkeypatch.setattr(
        main,
        "session_gate",
        lambda *_, **__: (_ for _ in ()).throw(ValueError("bad timezone")),
    )

    skip, payload = main._should_skip_for_market_hours()

    assert skip is True
    assert payload is not None
    assert payload["skip_reason"] == "market_closed"
    assert "ValueError" in payload["market_hours_check_error"]
