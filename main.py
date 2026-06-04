"""Cloud Run entrypoint for Firstrade platform validation and dry-run cycles."""

from __future__ import annotations

import os
import traceback
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from application.firstrade_client import (
    FirstradeBrokerClient,
    FirstradeCredentials,
    FirstradePlatformError,
    is_live_trading_enabled,
    mask_account_id,
)
from application.rebalance_service import run_strategy_cycle
from application.session_check_service import run_session_check
from notifications.telegram import build_sender
from strategy_registry import get_platform_profile_status_matrix

app = Flask(__name__)


def _flag(name: str, default: str = "false") -> bool:
    return (os.getenv(name, default) or "").strip().lower() == "true"


def _split_env_list(value: str | None) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in str(value or "").replace(";", ",").split(",")
        if item.strip()
    )


def _telegram_notification_targets() -> tuple[tuple[str, str], ...]:
    targets: list[tuple[str, str]] = []
    main_token = os.getenv("TELEGRAM_TOKEN")
    main_chat_id = os.getenv("GLOBAL_TELEGRAM_CHAT_ID")
    if main_token and main_chat_id:
        targets.append((main_token, main_chat_id))

    seen: set[tuple[str, str]] = set()
    unique_targets: list[tuple[str, str]] = []
    for target in targets:
        if target in seen:
            continue
        seen.add(target)
        unique_targets.append(target)
    return tuple(unique_targets)


def _runtime_error_notification_message(exc: Exception) -> str:
    error_text = f"{type(exc).__name__}: {exc}"
    if len(error_text) > 1200:
        error_text = error_text[:1197] + "..."
    return "\n".join(
        (
            "Firstrade strategy run failed",
            f"service: {os.getenv('K_SERVICE') or 'firstrade-quant-service'}",
            f"revision: {os.getenv('K_REVISION') or '<unknown>'}",
            f"route: {request.method} {request.path}",
            f"strategy: {os.getenv('STRATEGY_PROFILE') or '<unset>'}",
            f"account_scope: {os.getenv('ACCOUNT_REGION') or '<unset>'}",
            f"error: {error_text}",
        )
    )


def _notify_runtime_error(exc: Exception) -> bool:
    targets = _telegram_notification_targets()
    if not targets:
        print(
            "Firstrade runtime error notification skipped: no Telegram target configured.",
            flush=True,
        )
        return False

    message = _runtime_error_notification_message(exc)
    attempted = False
    for token, chat_id in targets:
        attempted = True
        try:
            build_sender(token, chat_id)(message)
        except Exception as send_exc:  # pragma: no cover - build_sender normally handles this.
            print(f"Firstrade runtime error Telegram send failed: {send_exc}", flush=True)
    return attempted


def _handle_strategy_run_exception(exc: Exception) -> bool:
    print(f"Firstrade strategy run failed: {type(exc).__name__}: {exc}", flush=True)
    traceback.print_exc()
    return _notify_runtime_error(exc)


@app.get("/")
def health():
    return jsonify(
        {
            "service": "firstrade-platform",
            "api_kind": "unofficial-reverse-engineered",
            "strategy_domain": "us_equity",
            "live_trading_enabled": is_live_trading_enabled(),
            "smoke_on_http": _flag("FIRSTRADE_RUN_SMOKE_ON_HTTP"),
            "session_check_on_http": _flag("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP"),
            "strategy_run_on_http": _flag("FIRSTRADE_RUN_STRATEGY_ON_HTTP"),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.get("/profiles")
def profiles():
    return jsonify(
        {
            "platform": "firstrade",
            "strategy_domain": "us_equity",
            "profiles": get_platform_profile_status_matrix(),
        }
    )


@app.get("/smoke")
def smoke():
    if not _flag("FIRSTRADE_RUN_SMOKE_ON_HTTP"):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Set FIRSTRADE_RUN_SMOKE_ON_HTTP=true to allow HTTP-triggered quote validation.",
                }
            ),
            403,
        )
    try:
        credentials = FirstradeCredentials.from_env()
        client = FirstradeBrokerClient(
            credentials,
            live_trading_enabled=is_live_trading_enabled(),
        ).connect()
        account = client.select_account(os.getenv("FIRSTRADE_ACCOUNT") or None)
        symbol = os.getenv("FIRSTRADE_SMOKE_SYMBOL", "SPY")
        return jsonify(
            {
                "ok": True,
                "api_kind": "unofficial-reverse-engineered",
                "selected_account": mask_account_id(account),
                "quote": client.get_quote(account, symbol),
            }
        )
    except FirstradePlatformError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/session-check")
@app.get("/session-check")
def session_check():
    if not _flag("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP"):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": (
                        "Set FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP=true to allow HTTP-triggered "
                        "read-only Firstrade session and account-state checks."
                    ),
                }
            ),
            403,
        )
    try:
        return jsonify(run_session_check())
    except FirstradePlatformError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@app.post("/")
@app.post("/run")
@app.get("/run")
def run_strategy():
    if not _flag("FIRSTRADE_RUN_STRATEGY_ON_HTTP"):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": (
                        "Set FIRSTRADE_RUN_STRATEGY_ON_HTTP=true to allow HTTP-triggered "
                        "strategy evaluation and guarded order routing."
                    ),
                }
            ),
            403,
        )
    try:
        return jsonify(run_strategy_cycle())
    except (FirstradePlatformError, EnvironmentError, ValueError) as exc:
        notification_attempted = _handle_strategy_run_exception(exc)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": str(exc),
                    "runtime_error_notification_attempted": notification_attempted,
                }
            ),
            500,
        )
    except Exception as exc:
        notification_attempted = _handle_strategy_run_exception(exc)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "runtime_error_notification_attempted": notification_attempted,
                }
            ),
            500,
        )


@app.post("/precheck")
@app.get("/precheck")
def precheck():
    return health()


@app.post("/probe")
@app.get("/probe")
def probe():
    return health()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
