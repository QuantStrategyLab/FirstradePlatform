from __future__ import annotations

import json
import http.client
import logging
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from firstrade.account import FTSession
from firstrade.exceptions import AccountResponseError, LoginResponseError

from application.firstrade_client import (
    FirstradeBrokerClient,
    FirstradeCredentials,
    FirstradeMfaRequired,
    FirstradeSafetyError,
)
from application.session_check_service import run_forced_session_renewal


@pytest.mark.parametrize("failure_stage", ["login", "seed_verify", "code_verify", "account_read"])
@pytest.mark.parametrize("error_kind", ["sdk", "unhandled"])
def test_renewal_route_contains_exceptions_without_logging_secrets(
    tmp_path, monkeypatch, caplog, capsys, failure_stage, error_kind,
):
    import main

    # Exercise Flask's production error handling and the pinned SDK with synthetic data only.
    private = ("synthetic-account-private", "246802", "A" * 32,
               "synthetic-password-private", "synthetic-token-private", "synthetic-chain-private")
    payload = " ".join(private[:-1])
    stages = []
    sessions = []
    monkeypatch.setenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", "true")
    monkeypatch.setitem(main.app.config, "TESTING", False)
    monkeypatch.setitem(main.app.config, "PROPAGATE_EXCEPTIONS", False)
    monkeypatch.setattr("pyotp.TOTP.now", lambda _self: private[1])
    monkeypatch.setattr(
        "requests.Session.request",
        lambda *_a, **_kw: pytest.fail("Unexpected network request"),
    )

    def fail(error_type):
        try:
            raise ValueError(private[-1])
        except ValueError as cause:
            raise error_type(payload) from cause

    class Session(FTSession):
        def __init__(self, **kwargs):
            assert kwargs["debug"] is False
            super().__init__(**kwargs, save_session=False)
            sessions.append(self)

        def _load_cookies(self):
            return ""

        def _request(self, method, url, **kwargs):
            if method == "get":
                return SimpleNamespace()
            stage = "login" if "password" in kwargs.get("data", {}) else failure_stage
            stages.append(stage)
            if stage == failure_stage:
                if error_kind == "unhandled":
                    fail(RuntimeError)
                # Let the actual SDK raise LoginResponseError from the provider response.
                return SimpleNamespace(status_code=200, json=lambda: {"error": payload})
            body = {"error": "", "ftat": private[4], "sid": "synthetic-sid"}
            if failure_stage in {"seed_verify", "code_verify"}:
                body = {"error": "", "mfa": True, "t_token": private[4]}
            return SimpleNamespace(status_code=200, json=lambda: body)

    def account_data(_session):
        stages.append("account_read")
        assert failure_stage == "account_read"
        fail(AccountResponseError if error_kind == "sdk" else RuntimeError)

    credentials = FirstradeCredentials(
        username="synthetic-user", password=private[3], cookie_dir=str(tmp_path),
        mfa_secret=private[2] if failure_stage == "seed_verify" else "",
        mfa_code=private[1] if failure_stage == "code_verify" else "",
        reuse_session=True, persist_session_cache=True, debug=True,
    )

    def factory(credentials, **kwargs):
        return FirstradeBrokerClient(
            credentials, **kwargs, session_factory=Session, account_data_factory=account_data,
        )

    monkeypatch.setattr(main, "run_forced_session_renewal", lambda: run_forced_session_renewal(
        credentials=credentials, client_factory=factory, env_reader=lambda *_a: None,
    ))
    caplog.set_level(logging.DEBUG)
    try:
        response = main.app.test_client().post("/session-renew")
    finally:
        for session in sessions:
            session.session.close()

    captured = capsys.readouterr()
    for marker in private:
        assert marker not in captured.out + captured.err + caplog.text + response.get_data(as_text=True)
    assert response.status_code == 503
    assert response.get_json() == {"ok": False, "error": "session_renew_failed"}
    assert stages == (["login"] if failure_stage == "login" else ["login", failure_stage])
    records = [record for record in caplog.records if record.name == main.app.logger.name]
    assert [record.getMessage() for record in records] == ["session_renew failed reason=session_renew_failed"]
    assert all(record.exc_info is None and record.exc_text is None and record.stack_info is None
               for record in caplog.records)
    event = json.loads(captured.out.strip())
    assert event == {
        "event": "firstrade_session_renew_auth",
        "challenge_required": "unknown" if failure_stage == "login" else
                              "no" if failure_stage == "account_read" else "yes",
        "method": "seed-auto" if failure_stage == "seed_verify" else "none",
        "outcome": "success" if failure_stage == "account_read" else "failed",
    }


@pytest.mark.parametrize(
    "scenario,challenge,method,outcome",
    [
        ("no_challenge", "no", "none", "success"),
        ("unused_seed", "no", "none", "success"),
        ("seed", "yes", "seed-auto", "success"),
        ("seed_failure", "yes", "seed-auto", "failed"),
        ("pin_before_seed", "yes", "none", "success"),
        ("otp_before_seed", "yes", "none", "success"),
        ("code_required", "yes", "none", "mfa_required"),
        ("code", "yes", "none", "success"),
        ("code_failure", "yes", "none", "failed"),
        ("login_failure", "unknown", "none", "failed"),
        ("account_failure", "no", "none", "success"),
        ("cache_failure", "no", "none", "success"),
    ],
)
def test_renewal_observes_pinned_sdk_branches_without_logging_secrets(
    tmp_path, monkeypatch, capsys, caplog, scenario, challenge, method, outcome,
):
    # All values and HTTP responses are synthetic. No broker or cloud calls.
    seed = "A" * 32
    code = "246802"
    account = "12345678"
    private = (seed, code, account, "synthetic-password", "synthetic-ftat", "synthetic-sid",
               "synthetic-access-token", "synthetic-challenge-token", "synthetic-provider-error")
    private_error = " ".join(private)
    monkeypatch.setattr("pyotp.TOTP.now", lambda _self: code)
    monkeypatch.setattr(
        "requests.Session.request",
        lambda *_a, **_kw: pytest.fail("Unexpected network request"),
    )
    sessions = []
    attempts = []
    challenged = challenge == "yes"

    class Session(FTSession):
        def __init__(self, **kwargs):
            assert kwargs["debug"] is False
            super().__init__(**kwargs, save_session=False)
            sessions.append(self)

        def _load_cookies(self):
            return private[4] if not challenged else ""

        def _request(self, method, url, **kwargs):
            data = kwargs.get("data", {})
            if method == "get":
                return SimpleNamespace()
            if "password" in data:
                attempts.append("login")
                if scenario == "login_failure":
                    raise RuntimeError(private_error)
                body = {"error": "", "ftat": private[4], "sid": private[5]}
                if challenged:
                    body = {"error": "", "mfa": scenario != "otp_before_seed",
                            "t_token": private[7], "otp": [
                                {"channel": "email", "recipientMask": "x@example.invalid", "recipientId": "fake"},
                            ]}
                return SimpleNamespace(status_code=200, json=lambda: body)
            attempts.append("verify")
            body = {"error": private_error if scenario in {"seed_failure", "code_failure"} else "",
                    "ftat": private[4], "sid": private[5], "verificationSid": "synthetic-verification"}
            return SimpleNamespace(status_code=200, json=lambda: body)

    class Data:
        account_numbers = [account]

        def __init__(self, _session):
            attempts.append("account")
            if scenario == "account_failure":
                raise RuntimeError(private_error)

        def get_account_balances(self, _account):
            return {"cash": 1}

    class Store:
        def write_json(self, *_args):
            attempts.append("cache")
            if scenario == "cache_failure":
                raise RuntimeError(private_error)
            return True

    credentials = FirstradeCredentials(
        username="synthetic-user", password=private[3], cookie_dir=str(tmp_path),
        mfa_secret=seed if scenario in {"seed", "seed_failure", "unused_seed", "pin_before_seed", "otp_before_seed"} else "",
        mfa_code=code if scenario in {"code", "code_failure", "otp_before_seed"} else "",
        pin="synthetic-pin" if scenario == "pin_before_seed" else "",
        email="x@example.invalid" if scenario == "otp_before_seed" else "",
        reuse_session=True, persist_session_cache=True, debug=True,
    )

    def factory(credentials, **kwargs):
        return FirstradeBrokerClient(
            credentials, **kwargs, session_factory=Session, account_data_factory=Data,
            session_cache_store=Store(),
        )

    def renew():
        return run_forced_session_renewal(
            credentials=credentials, client_factory=factory, env_reader=lambda *_a: None,
        )

    if outcome != "success" or scenario in {"account_failure", "cache_failure"}:
        error = (FirstradeMfaRequired if outcome == "mfa_required" else
                 FirstradeSafetyError if scenario == "cache_failure" else
                 LoginResponseError if scenario in {"seed_failure", "code_failure"} else RuntimeError)
        with pytest.raises(error):
            renew()
    else:
        assert renew()["session_renewed"] is True

    output = capsys.readouterr()
    entries = [json.loads(line) for line in output.out.splitlines() if line.startswith("{")]
    assert entries == [{"event": "firstrade_session_renew_auth", "challenge_required": challenge,
                        "method": method, "outcome": outcome}]
    for value in (*private, "5678", "synthetic-pin", "x@example.invalid"):
        assert value not in output.out + output.err + caplog.text
    assert attempts.count("login") == 1
    assert attempts.count("verify") == (0 if scenario in {"code_required", "login_failure"} or not challenged else 1)
    if scenario in {"account_failure", "cache_failure"}:
        assert attempts[-1] == ("account" if scenario == "account_failure" else "cache")
    assert sessions[0]._handle_mfa.__func__ is FTSession._handle_mfa
    assert sessions[0]._handle_secret_mfa.__func__ is FTSession._handle_secret_mfa
    sessions[0].session.close()


def test_ordinary_connect_keeps_debug_setting_and_emits_no_renewal_event(tmp_path, capsys):
    class Session:
        def __init__(self, **kwargs):
            assert kwargs["debug"] is True

        def login(self):
            return False

    FirstradeBrokerClient(
        FirstradeCredentials(username="u", password="p", cookie_dir=str(tmp_path), debug=True),
        session_factory=Session, account_data_factory=lambda _session: object(),
    ).connect()
    assert "firstrade_session_renew_auth" not in capsys.readouterr().out


@pytest.mark.parametrize("fail_at", ["write", "flush"])
@pytest.mark.parametrize("outcome", ["success", "failed", "mfa_required"])
def test_log_io_failure_preserves_authentication_result(tmp_path, monkeypatch, fail_at, outcome):
    original_error = RuntimeError("synthetic auth failure")

    class Session:
        def __init__(self, **_kwargs):
            pass

        def login(self):
            if outcome == "failed":
                raise original_error
            return outcome == "mfa_required"

    class BrokenOutput:
        def write(self, _value):
            if fail_at == "write":
                raise OSError("synthetic log failure")

        def flush(self):
            if fail_at == "flush":
                raise OSError("synthetic log failure")

    client = FirstradeBrokerClient(
        FirstradeCredentials(username="u", password="p", cookie_dir=str(tmp_path)),
        session_factory=Session, account_data_factory=lambda _session: object(),
    )
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", BrokenOutput())
        if outcome == "success":
            assert client.connect(force_login=True, defer_cache_save=True) is client
        else:
            with pytest.raises(RuntimeError if outcome == "failed" else FirstradeMfaRequired) as caught:
                client.connect(force_login=True, defer_cache_save=True)
            if outcome == "failed":
                assert caught.value is original_error


@pytest.mark.parametrize("failure", [None, "auth", "balance", "cache"])
def test_renewal_quiets_inherited_sdk_debug_through_cache_and_restores_state(
    tmp_path, monkeypatch, capsys, caplog, failure,
):
    wire_payload = b"synthetic-private-password 246802 12345678 synthetic-private-token"
    original_debuglevel = http.client.HTTPConnection.debuglevel
    loggers = [logging.getLogger(name) for name in (
        "", "requests.packages.urllib3", "urllib3", "urllib3.connectionpool",
    )]
    original_levels = [logger.level for logger in loggers]
    sdk_logger = logging.getLogger("requests.packages.urllib3")
    original_propagate = sdk_logger.propagate
    renewing = False
    stages = []

    def wire(stage):
        stages.append(stage)
        connection = http.client.HTTPConnection("example.invalid")
        connection.sock = SimpleNamespace(sendall=lambda _data: None)
        connection.send(wire_payload)
        logging.getLogger("urllib3.connectionpool").debug(wire_payload.decode())

    class Session(FTSession):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, save_session=False)

        def _load_cookies(self):
            return "synthetic-ftat"

        def _request(self, method, url, **kwargs):
            wire("login")
            if renewing and failure == "auth":
                raise RuntimeError("synthetic auth failure")
            return SimpleNamespace(status_code=200, json=lambda: {
                "error": "", "ftat": "synthetic-ftat", "sid": "synthetic-sid",
            })

    class Data:
        account_numbers = ["12345678"]

        def __init__(self, _session):
            wire("account")

        def get_account_balances(self, _account):
            wire("balance")
            if failure == "balance":
                raise RuntimeError("synthetic balance failure")
            return {"cash": 1}

    class Store:
        def write_json(self, *_args):
            wire("cache")
            if failure == "cache":
                raise RuntimeError("synthetic cache failure")
            return True

    def factory(credentials, **kwargs):
        return FirstradeBrokerClient(
            credentials, **kwargs, session_factory=Session, account_data_factory=Data,
            session_cache_store=Store(),
        )

    monkeypatch.setattr("requests.Session.request", lambda *_a, **_kw: pytest.fail("Unexpected network request"))
    credentials = FirstradeCredentials(
        username="u", password="p", cookie_dir=str(tmp_path), debug=True,
    )
    try:
        # Reproduce the pinned SDK's process-wide debug mutation via an ordinary login.
        ordinary = factory(credentials).connect()
        assert wire_payload.decode() in capsys.readouterr().out
        ordinary.session.session.close()
        caplog.clear()
        assert http.client.HTTPConnection.debuglevel == 1
        inherited_levels = [logger.level for logger in loggers]
        renewing = True
        stages.clear()

        def renew():
            return run_forced_session_renewal(
                credentials=replace(credentials, reuse_session=True, persist_session_cache=True),
                client_factory=factory, env_reader=lambda *_a: None,
            )

        if failure:
            with pytest.raises(FirstradeSafetyError if failure == "cache" else RuntimeError):
                renew()
        else:
            assert renew()["session_renewed"] is True
        captured = capsys.readouterr()
        assert "synthetic-private" not in captured.out + captured.err + caplog.text
        assert "246802" not in captured.out + captured.err + caplog.text
        assert "5678" not in captured.out + captured.err + caplog.text
        assert http.client.HTTPConnection.debuglevel == 1
        assert [logger.level for logger in loggers] == inherited_levels
        assert stages[-1] == {None: "cache", "auth": "login", "balance": "balance", "cache": "cache"}[failure]
    finally:
        http.client.HTTPConnection.debuglevel = original_debuglevel
        for logger, level in zip(loggers, original_levels):
            logger.setLevel(level)
        sdk_logger.propagate = original_propagate
