"""Small Telegram notification helpers for FirstradePlatform."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def build_sender(token: str | None, chat_id: str | None, *, requests_module=None):
    if requests_module is None:
        import requests as requests_module

    def send_tg_message(message: str) -> None:
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests_module.post(url, json={"chat_id": chat_id, "text": message}, timeout=15)
        except Exception as exc:
            print(f"Telegram send failed: {exc}", flush=True)

    return send_tg_message


def render_cycle_summary(result: Mapping[str, Any], *, lang: str = "en") -> str:
    use_zh = str(lang or "").lower().startswith("zh")
    submitted = list(result.get("submitted_orders") or ())
    skipped = list(result.get("skipped_orders") or ())
    execution = dict(result.get("execution") or {})
    allocation = dict(result.get("allocation") or {})
    header = "Firstrade 策略运行" if use_zh else "Firstrade Strategy Cycle"
    dry_run_label = "模拟" if use_zh else "dry-run"
    live_label = "实盘" if use_zh else "live"
    no_trade = "无需调仓" if use_zh else "no rebalance needed"
    submitted_label = "订单" if use_zh else "orders"
    skipped_label = "跳过" if use_zh else "skipped"
    mode = dry_run_label if result.get("dry_run_only") else live_label
    lines = [
        header,
        f"mode: {mode}",
        f"profile: {result.get('strategy_profile')}",
        f"account: {result.get('account')}",
    ]
    signal = execution.get("signal_display")
    status = execution.get("status_display")
    if status:
        lines.append(f"status: {status}")
    if signal:
        lines.append(f"signal: {signal}")
    targets = dict(allocation.get("targets") or {})
    if targets:
        target_text = ", ".join(
            f"{symbol}=${float(value):,.2f}"
            for symbol, value in sorted(targets.items())
        )
        lines.append(f"targets: {target_text}")
    if submitted:
        order_text = ", ".join(
            f"{order.get('side')} {order.get('symbol')} x{order.get('quantity')}"
            for order in submitted
        )
        lines.append(f"{submitted_label}: {order_text}")
    else:
        lines.append(no_trade)
    if skipped:
        lines.append(f"{skipped_label}: {len(skipped)}")
    return "\n".join(str(line) for line in lines if str(line).strip())
