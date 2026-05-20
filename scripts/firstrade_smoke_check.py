#!/usr/bin/env python3
"""Bounded Firstrade API smoke check.

Default behavior:
- login
- list masked accounts
- fetch one quote
- optionally fetch balances and positions
- do not place or preview any order unless explicitly requested
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.firstrade_client import (  # noqa: E402
    FirstradeBrokerClient,
    FirstradeCredentials,
    FirstradePlatformError,
    FirstradeSafetyError,
    StockOrderRequest,
    is_live_trading_enabled,
    mask_account_id,
)


def _json_default(value: Any) -> str:
    return str(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Firstrade unofficial API smoke check.")
    parser.add_argument("--account", default=os.getenv("FIRSTRADE_ACCOUNT", ""))
    parser.add_argument("--symbol", default="SPY", help="Symbol to quote or validate.")
    parser.add_argument("--quote-only", action="store_true", help="Login and quote only.")
    parser.add_argument(
        "--include-balances",
        action="store_true",
        help="Also fetch raw balance payload for the selected account.",
    )
    parser.add_argument(
        "--include-positions",
        action="store_true",
        help="Also fetch raw positions payload for the selected account.",
    )
    parser.add_argument("--preview-order", action="store_true", help="Build a Firstrade order preview.")
    parser.add_argument("--live-order", action="store_true", help="Submit a live order after local safety gates.")
    parser.add_argument("--side", choices=["buy", "sell"], default="buy")
    parser.add_argument("--quantity", type=int)
    parser.add_argument("--notional-usd", type=float)
    parser.add_argument("--price-type", choices=["market", "limit", "stop", "stop_limit"], default="market")
    parser.add_argument("--duration", choices=["day", "day_ext", "overnight", "gt90"], default="day")
    parser.add_argument("--limit-price", type=float)
    parser.add_argument("--stop-price", type=float)
    parser.add_argument("--max-notional-usd", type=float, default=25.0)
    parser.add_argument(
        "--yes-i-understand-unofficial-api-risk",
        action="store_true",
        help="Required with --live-order. Confirms this is an unofficial reverse-engineered API.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.live_order and args.preview_order:
        raise FirstradeSafetyError("Use either --preview-order or --live-order, not both.")
    if args.quote_only and (args.preview_order or args.live_order):
        raise FirstradeSafetyError("--quote-only cannot be combined with order flags.")

    credentials = FirstradeCredentials.from_env()
    client = FirstradeBrokerClient(
        credentials,
        live_trading_enabled=is_live_trading_enabled(),
    ).connect()
    account = client.select_account(args.account or None)
    result: dict[str, Any] = {
        "api_kind": "unofficial-reverse-engineered",
        "live_trading_enabled": is_live_trading_enabled(),
        "accounts": client.list_account_summaries(),
        "selected_account": mask_account_id(account),
        "quote": client.get_quote(account, args.symbol),
    }
    if args.include_balances:
        result["balances"] = client.get_balances(account)
    if args.include_positions:
        result["positions"] = client.get_positions(account)

    if args.preview_order or args.live_order:
        request = StockOrderRequest(
            account=account,
            symbol=args.symbol,
            side=args.side,
            quantity=args.quantity,
            notional_usd=args.notional_usd,
            price_type=args.price_type,
            duration=args.duration,
            limit_price=args.limit_price,
            stop_price=args.stop_price,
            max_notional_usd=args.max_notional_usd,
        )
        dry_run = not args.live_order
        result["order_mode"] = "preview" if dry_run else "live"
        result["order_response"] = client.place_stock_order(
            request,
            dry_run=dry_run,
            explicit_live_ack=args.yes_i_understand_unofficial_api_risk,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FirstradePlatformError, FirstradeSafetyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
