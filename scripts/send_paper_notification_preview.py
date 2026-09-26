#!/usr/bin/env python3
"""Send a bounded FirstradePlatform PAPER Telegram notification preview pack.

Renders synthetic compact messages via existing notification renderer,
translator, and Telegram sender. Does not trade, read Firstrade accounts,
positions, or quotes; does not import or call broker/order/Cloud Run
production interfaces; and does not change production configuration.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notifications.telegram import (  # noqa: E402
    build_sender,
    build_strategy_display_name,
    build_translator,
    render_cycle_notification,
)

_MAX_PREVIEW_MESSAGES = 6
_PREVIEW_STRATEGY_PROFILE = "tqqq_growth_income"
_PREVIEW_EXTRA_LINES = (
    "🧪 【PREVIEW】PAPER notification preview",
    "synthetic / 合成样例 · 不会下单 · No order will be placed",
)
_SYNTHETIC_SYMBOL = "PREVIEW"


def _resolve_locale(raw: str | None = None) -> str:
    value = str(raw or os.environ.get("QSL_NOTIFY_LANG") or os.environ.get("NOTIFY_LANG") or "zh")
    value = value.strip().lower()
    return "en" if value.startswith("en") else "zh"


def _split_chat_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [
        part.strip()
        for part in str(raw).replace(";", ",").replace("\n", ",").split(",")
        if part.strip()
    ]


def resolve_telegram_token() -> str:
    direct_token = (os.environ.get("TELEGRAM_TOKEN") or os.environ.get("TG_TOKEN") or "").strip()
    if direct_token:
        return direct_token
    secret_name = (os.environ.get("TELEGRAM_TOKEN_SECRET_NAME") or "").strip()
    if not secret_name:
        return ""
    result = subprocess.run(
        ["gcloud", "secrets", "versions", "access", "latest", "--secret", secret_name],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def resolve_telegram_chat_id() -> str:
    chats = _split_chat_ids(
        os.environ.get("QSL_GLOBAL_TELEGRAM_CHAT_ID")
        or os.environ.get("GLOBAL_TELEGRAM_CHAT_ID")
    )
    return chats[0] if chats else ""


def _with_preview_markers(body: str) -> str:
    return "\n".join(("[PAPER]", body, *_PREVIEW_EXTRA_LINES))


def _empty_portfolio() -> dict[str, Any]:
    return {
        "total_equity": 0.0,
        "liquid_cash": 0.0,
        "portfolio_rows": (),
        "market_values": {},
        "quantities": {},
    }


def _base_result(*, dry_run_only: bool, strategy_display_name: str) -> dict[str, Any]:
    return {
        "account": "PAPER",
        "strategy_profile": _PREVIEW_STRATEGY_PROFILE,
        "strategy_display_name": strategy_display_name,
        "dry_run_only": dry_run_only,
        "portfolio": _empty_portfolio(),
        "allocation": {"targets": {}},
        "execution": {"cash_only_execution": True},
        "submitted_orders": [],
        "skipped_orders": [],
    }


def build_preview_messages(*, locale: str | None = None) -> list[str]:
    """Build at most six synthetic compact PAPER preview messages."""

    resolved_locale = _resolve_locale(locale)
    translator = build_translator(resolved_locale)
    strategy_name = build_strategy_display_name(resolved_locale, translator)(
        _PREVIEW_STRATEGY_PROFILE,
        fallback_name="TQQQ Growth Income",
    )
    account_line = translator("account_label", account="PAPER")

    heartbeat = render_cycle_notification(
        {
            **_base_result(dry_run_only=True, strategy_display_name=strategy_name),
        },
        lang=resolved_locale,
    ).compact_text

    dry_run = render_cycle_notification(
        {
            **_base_result(dry_run_only=True, strategy_display_name=strategy_name),
            "portfolio": {
                "total_equity": 0.0,
                "liquid_cash": 0.0,
                "portfolio_rows": ((_SYNTHETIC_SYMBOL,),),
                "market_values": {_SYNTHETIC_SYMBOL: 0.0},
                "quantities": {_SYNTHETIC_SYMBOL: 0},
            },
            "allocation": {"targets": {_SYNTHETIC_SYMBOL: 100.0}},
            "submitted_orders": [
                {
                    "side": "buy",
                    "symbol": _SYNTHETIC_SYMBOL,
                    "quantity": 0,
                    "order_type": "limit",
                    "limit_price": 0,
                }
            ],
        },
        lang=resolved_locale,
    ).compact_text

    pending = render_cycle_notification(
        {
            **_base_result(dry_run_only=False, strategy_display_name=strategy_name),
            "allocation": {"targets": {_SYNTHETIC_SYMBOL: 100.0}},
            "submitted_orders": [
                {
                    "side": "buy",
                    "symbol": _SYNTHETIC_SYMBOL,
                    "quantity": 0,
                    "order_type": "limit",
                    "limit_price": 0,
                    "broker_order_id": "preview-synthetic-pending",
                }
            ],
        },
        lang=resolved_locale,
    ).compact_text
    pending = "\n".join((pending, "synthetic PREVIEW pending confirmation / 订单待确认"))

    filled_order = (
        f"📈 {translator('order_type_market')}{translator('side_buy')} {_SYNTHETIC_SYMBOL}: "
        f"{translator('quantity_shares', quantity='0')}"
        f"{translator('order_id_suffix', order_id='preview-synthetic-filled')}"
    )
    filled = "\n".join(
        (
            translator("rebalance_title"),
            translator("strategy_label", name=strategy_name),
            translator("dry_run_banner"),
            filled_order,
            "synthetic PREVIEW filled / 成交确认",
        )
    )

    rejected = render_cycle_notification(
        {
            **_base_result(dry_run_only=True, strategy_display_name=strategy_name),
            "allocation": {"targets": {_SYNTHETIC_SYMBOL: 100.0}},
            "skipped_orders": [
                {"symbol": _SYNTHETIC_SYMBOL, "reason": "broker_rejected"},
            ],
        },
        lang=resolved_locale,
    ).compact_text
    rejected = "\n".join((rejected, "synthetic PREVIEW reject / 拒单异常"))

    unknown_status = "\n".join(
        (
            translator("runtime_failure_title"),
            translator("strategy_label", name=strategy_name),
            account_line,
            translator("dry_run_banner"),
            f"status={translator('strategy_plugin_route_unknown_route')}",
            "synthetic PREVIEW unknown status / 未知状态",
        )
    )

    messages = [
        _with_preview_markers(heartbeat),
        _with_preview_markers(dry_run),
        _with_preview_markers(pending),
        _with_preview_markers(filled),
        _with_preview_markers(rejected),
        _with_preview_markers(unknown_status),
    ]
    if len(messages) > _MAX_PREVIEW_MESSAGES:
        raise RuntimeError(
            f"preview message count {len(messages)} exceeds cap {_MAX_PREVIEW_MESSAGES}"
        )
    return messages


def send_preview(*, locale: str | None = None, send_fn=None, requests_module=None) -> bool:
    messages = build_preview_messages(locale=locale)
    token = resolve_telegram_token()
    chat_id = resolve_telegram_chat_id()
    if not token or not chat_id:
        print(
            "Notification preview not sent: Telegram target is not configured.",
            file=sys.stderr,
        )
        return False

    sender = send_fn or build_sender(token, chat_id, requests_module=requests_module)
    for message in messages:
        if not sender(message):
            print("Notification preview delivery failed.", file=sys.stderr)
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send a bounded FirstradePlatform PAPER Telegram notification preview pack."
    )
    parser.add_argument(
        "--locale",
        default=os.environ.get("NOTIFY_LANG"),
        help="Optional notification locale override (zh/en). Defaults to NOTIFY_LANG.",
    )
    args = parser.parse_args(argv)

    # Fail closed: this path never enables a production runtime target.
    if (os.environ.get("RUNTIME_TARGET_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }:
        print(
            "Notification preview refused: RUNTIME_TARGET_ENABLED must stay disabled.",
            file=sys.stderr,
        )
        return 1

    delivered = send_preview(locale=args.locale)
    if not delivered:
        return 1
    print(
        "Notification preview delivered bounded synthetic PAPER pack "
        f"(at most {_MAX_PREVIEW_MESSAGES} messages; no orders)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
