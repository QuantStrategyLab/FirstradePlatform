from __future__ import annotations

import json
import http.client
import logging
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from firstrade.account import FTSession
from firstrade.exceptions import LoginResponseError

from application.firstrade_client import (
    FirstradeBrokerClient,
    FirstradeCredentials,
    FirstradeMfaRequired,
    FirstradeSafetyError,
)
from application.session_check_service import run_forced_session_renewal


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
