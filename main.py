"""Cloud Run entrypoint for Firstrade platform validation and dry-run cycles."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import Flask, jsonify

from application.firstrade_client import (
    FirstradeBrokerClient,
    FirstradeCredentials,
    FirstradePlatformError,
    is_live_trading_enabled,
    mask_account_id,
)
from application.rebalance_service import run_strategy_cycle
from strategy_registry import get_platform_profile_status_matrix

app = Flask(__name__)


def _flag(name: str, default: str = "false") -> bool:
    return (os.getenv(name, default) or "").strip().lower() == "true"


@app.get("/")
def health():
    return jsonify(
        {
            "service": "firstrade-platform",
            "api_kind": "unofficial-reverse-engineered",
            "strategy_domain": "us_equity",
            "live_trading_enabled": is_live_trading_enabled(),
            "smoke_on_http": _flag("FIRSTRADE_RUN_SMOKE_ON_HTTP"),
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
        return jsonify({"ok": False, "error": str(exc)}), 500
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@app.get("/precheck")
def precheck():
    return health()


@app.get("/probe")
def probe():
    return health()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
