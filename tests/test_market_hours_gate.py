from __future__ import annotations

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
