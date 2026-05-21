"""Small Telegram notification helpers for FirstradePlatform."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


SEPARATOR = "━━━━━━━━━━━━━━━━━━"


I18N = {
    "zh": {
        "rebalance_title": "🔔 【调仓指令】",
        "heartbeat_title": "💓 【心跳检测】",
        "strategy_label": "🧭 策略: {name}",
        "account_label": "🆔 账户: {account}",
        "dry_run_banner": "🧪 模拟运行，本轮不提交真实订单",
        "account_overview_title": "📌 策略账户概览",
        "total_assets": "总资产（策略标的+现金）",
        "buying_power": "购买力",
        "reserved_cash": "预留现金",
        "investable_cash": "可投资现金",
        "holdings_title": "💼 策略持仓",
        "holding_line": "{symbol}: {market_value} / {quantity}",
        "quantity_shares": "{quantity}股",
        "same_trading_day": "当日执行",
        "next_trading_day": "次一交易日执行",
        "next_n_trading_days": "{count}个交易日后执行",
        "timing_line": "⏱ 执行时点: {value}",
        "market_status_line": "📊 市场状态: {status}",
        "signal_line": "🎯 信号: {signal}",
        "target_diff_summary": "调仓变化: {details}",
        "dry_run_buy_order": "🧪 模拟买单: {symbol} {quantity}",
        "dry_run_sell_order": "🧪 模拟卖单: {symbol} {quantity}",
        "submitted_buy_order": "已提交买单: {symbol} {quantity}",
        "submitted_sell_order": "已提交卖单: {symbol} {quantity}",
        "no_order_submitted": "未下单: 原因={reason}",
        "no_rebalance_needed": "✅ 无需调仓",
        "no_executable_orders": "无可执行订单",
        "signal_state_hold": "趋势持有",
        "signal_state_entry": "入场信号",
        "signal_state_reduce": "减仓信号",
        "signal_state_exit": "离场信号",
        "signal_state_idle": "等待信号",
        "skip_reason_below_trade_threshold": "低于调仓阈值",
        "skip_reason_quote_unavailable": "无法获取报价",
        "skip_reason_sell_quantity_zero": "卖出股数为0",
        "skip_reason_buy_quantity_zero": "买入股数为0",
        "skip_reason_unknown": "未知原因",
    },
    "en": {
        "rebalance_title": "🔔 【Rebalance Instruction】",
        "heartbeat_title": "💓 【Heartbeat】",
        "strategy_label": "🧭 Strategy: {name}",
        "account_label": "🆔 Account: {account}",
        "dry_run_banner": "🧪 Dry run only; no live orders submitted",
        "account_overview_title": "📌 Strategy Account",
        "total_assets": "Total assets",
        "buying_power": "Buying power",
        "reserved_cash": "Reserved cash",
        "investable_cash": "Investable cash",
        "holdings_title": "💼 Strategy Holdings",
        "holding_line": "{symbol}: {market_value} / {quantity}",
        "quantity_shares": "{quantity} shares",
        "same_trading_day": "same trading day",
        "next_trading_day": "next trading day",
        "next_n_trading_days": "next {count} trading days",
        "timing_line": "⏱ Timing: {value}",
        "market_status_line": "📊 Market: {status}",
        "signal_line": "🎯 Signal: {signal}",
        "target_diff_summary": "Target changes: {details}",
        "dry_run_buy_order": "🧪 Dry-run buy: {symbol} {quantity}",
        "dry_run_sell_order": "🧪 Dry-run sell: {symbol} {quantity}",
        "submitted_buy_order": "Submitted buy: {symbol} {quantity}",
        "submitted_sell_order": "Submitted sell: {symbol} {quantity}",
        "no_order_submitted": "No order submitted: reason={reason}",
        "no_rebalance_needed": "✅ No rebalance needed",
        "no_executable_orders": "no executable orders",
        "signal_state_hold": "Trend Hold",
        "signal_state_entry": "Entry Signal",
        "signal_state_reduce": "Reduce Signal",
        "signal_state_exit": "Exit Signal",
        "signal_state_idle": "Idle",
        "skip_reason_below_trade_threshold": "below trade threshold",
        "skip_reason_quote_unavailable": "quote unavailable",
        "skip_reason_sell_quantity_zero": "sell quantity rounds to 0",
        "skip_reason_buy_quantity_zero": "buy quantity rounds to 0",
        "skip_reason_unknown": "unknown reason",
    },
}


def build_translator(lang: str | None) -> Callable[..., str]:
    normalized = str(lang or "").lower()
    active_lang = "zh" if normalized.startswith("zh") else "en"

    def translate(key: str, **kwargs) -> str:
        template = I18N[active_lang].get(key, I18N["en"].get(key, key))
        return template.format(**kwargs) if kwargs else template

    return translate


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


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _format_money(value: Any) -> str:
    number = _safe_float(value)
    return "$0.00" if number is None else f"${number:,.2f}"


def _format_quantity(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "0"
    if float(number).is_integer():
        return str(int(number))
    return f"{number:g}"


def _format_shares(value: Any, *, translator: Callable[..., str]) -> str:
    return translator("quantity_shares", quantity=_format_quantity(value))


def _format_dashboard_lines(
    portfolio: Mapping[str, Any],
    execution: Mapping[str, Any],
    *,
    translator: Callable[..., str],
) -> list[str]:
    dashboard_text = str(execution.get("dashboard_text") or "").strip()
    if dashboard_text:
        return [line.rstrip() for line in dashboard_text.splitlines() if line.strip()]

    lines = [translator("account_overview_title")]
    total_equity = _safe_float(portfolio.get("total_equity"))
    if total_equity is not None:
        lines.append(f"  - {translator('total_assets')}: {_format_money(total_equity)}")
    buying_power = _safe_float(portfolio.get("liquid_cash"))
    if buying_power is not None:
        lines.append(f"  - {translator('buying_power')}: {_format_money(buying_power)}")
    reserved_cash = _safe_float(execution.get("reserved_cash"))
    if reserved_cash is not None:
        lines.append(f"  - {translator('reserved_cash')}: {_format_money(reserved_cash)}")
    investable_cash = _safe_float(execution.get("investable_cash"))
    if investable_cash is not None:
        lines.append(f"  - {translator('investable_cash')}: {_format_money(investable_cash)}")

    market_values = {
        str(symbol).upper(): float(value or 0.0)
        for symbol, value in dict(portfolio.get("market_values") or {}).items()
    }
    quantities = {
        str(symbol).upper(): value
        for symbol, value in dict(portfolio.get("quantities") or {}).items()
    }
    portfolio_rows = tuple(portfolio.get("portfolio_rows") or ())
    symbols: list[str] = []
    for row in portfolio_rows:
        if isinstance(row, (list, tuple)):
            symbols.extend(str(symbol).upper() for symbol in row)
        elif row:
            symbols.append(str(row).upper())
    if not symbols:
        symbols = sorted(market_values)
    if symbols:
        lines.append(translator("holdings_title"))
        for symbol in symbols:
            lines.append(
                "  - "
                + translator(
                    "holding_line",
                    symbol=symbol,
                    market_value=_format_money(market_values.get(symbol, 0.0)),
                    quantity=_format_shares(quantities.get(symbol, 0), translator=translator),
                )
            )
    return lines


def _localize_timing_contract(contract: Any, *, translator: Callable[..., str]) -> str:
    value = str(contract or "").strip()
    if value == "same_trading_day":
        return translator("same_trading_day")
    if value == "next_trading_day":
        return translator("next_trading_day")
    if value.startswith("next_") and value.endswith("_trading_days"):
        count_text = value.removeprefix("next_").removesuffix("_trading_days")
        if count_text.isdigit():
            return translator("next_n_trading_days", count=int(count_text))
    return value


def _format_timing_lines(execution: Mapping[str, Any], *, translator: Callable[..., str]) -> list[str]:
    signal_date = str(execution.get("signal_date") or "").strip()
    effective_date = str(execution.get("effective_date") or "").strip()
    contract = _localize_timing_contract(execution.get("execution_timing_contract"), translator=translator)
    if not signal_date and not effective_date and not contract:
        return []
    if signal_date and effective_date:
        value = f"{signal_date} -> {effective_date}"
    else:
        value = signal_date or effective_date or contract
    if contract and contract not in value:
        value = f"{value} ({contract})"
    return [translator("timing_line", value=value)]


def _first_summary(value: Any, *, translator: Callable[..., str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    summary = text.split(" | ", 1)[0].strip()
    key = f"signal_state_{summary.lower()}"
    translated = translator(key)
    return translated if translated != key else summary


def _format_signal_lines(execution: Mapping[str, Any], *, translator: Callable[..., str]) -> list[str]:
    status = _first_summary(execution.get("status_display"), translator=translator)
    signal = _first_summary(execution.get("signal_display"), translator=translator)
    lines = []
    if status and status != signal:
        lines.append(translator("market_status_line", status=status))
    if signal:
        lines.append(translator("signal_line", signal=signal))
    return lines


def _format_target_diff_lines(
    allocation: Mapping[str, Any],
    portfolio: Mapping[str, Any],
    *,
    translator: Callable[..., str],
) -> list[str]:
    targets = {
        str(symbol).upper(): float(value or 0.0)
        for symbol, value in dict(allocation.get("targets") or {}).items()
    }
    market_values = {
        str(symbol).upper(): float(value or 0.0)
        for symbol, value in dict(portfolio.get("market_values") or {}).items()
    }
    details = []
    for symbol in sorted(set(targets) | set(market_values)):
        delta = targets.get(symbol, 0.0) - market_values.get(symbol, 0.0)
        if abs(delta) < 0.005:
            continue
        details.append(f"{symbol} {delta:+,.2f} USD")
    if not details:
        return []
    return [translator("target_diff_summary", details=", ".join(details))]


def _format_order_lines(
    submitted: list[Mapping[str, Any]],
    *,
    dry_run_only: bool,
    translator: Callable[..., str],
) -> list[str]:
    lines = []
    for order in submitted:
        side = str(order.get("side") or "").lower()
        symbol = str(order.get("symbol") or "").upper()
        side_key = "buy" if side == "buy" else "sell"
        mode_key = "dry_run" if dry_run_only else "submitted"
        lines.append(
            translator(
                f"{mode_key}_{side_key}_order",
                symbol=symbol,
                quantity=_format_shares(order.get("quantity"), translator=translator),
            )
        )
    return lines


def _format_skipped_reason(skipped: list[Mapping[str, Any]], *, translator: Callable[..., str]) -> str:
    grouped: dict[str, list[str]] = {}
    for item in skipped:
        raw_reason = str(item.get("reason") or "unknown")
        key = f"skip_reason_{raw_reason}"
        reason = translator(key)
        if reason == key:
            reason = raw_reason or translator("skip_reason_unknown")
        symbol = str(item.get("symbol") or "").upper()
        grouped.setdefault(reason, [])
        if symbol:
            grouped[reason].append(symbol)
    parts = []
    for reason, symbols in grouped.items():
        parts.append(f"{reason}:{','.join(symbols)}" if symbols else reason)
    return ", ".join(parts) if parts else translator("no_executable_orders")


def render_cycle_summary(result: Mapping[str, Any], *, lang: str = "en") -> str:
    translator = build_translator(lang)
    submitted = list(result.get("submitted_orders") or ())
    skipped = list(result.get("skipped_orders") or ())
    execution = dict(result.get("execution") or {})
    allocation = dict(result.get("allocation") or {})
    portfolio = dict(result.get("portfolio") or {})
    dry_run_only = bool(result.get("dry_run_only"))
    target_diff_lines = _format_target_diff_lines(allocation, portfolio, translator=translator)
    has_meaningful_skip = any(
        str(item.get("reason") or "") != "below_trade_threshold"
        for item in skipped
    )
    has_rebalance_attempt = bool(submitted or target_diff_lines or has_meaningful_skip)
    strategy_name = str(result.get("strategy_display_name") or result.get("strategy_profile") or "").strip()
    account = str(result.get("account") or "").strip()
    lines = [translator("rebalance_title" if has_rebalance_attempt else "heartbeat_title")]
    if strategy_name:
        lines.append(translator("strategy_label", name=strategy_name))
    if account:
        lines.append(translator("account_label", account=account))
    if dry_run_only:
        lines.append(translator("dry_run_banner"))

    dashboard_lines = _format_dashboard_lines(portfolio, execution, translator=translator)
    if dashboard_lines:
        lines.append(SEPARATOR)
        lines.extend(dashboard_lines)
    lines.extend(_format_timing_lines(execution, translator=translator))
    lines.extend(_format_signal_lines(execution, translator=translator))
    lines.append(SEPARATOR)
    lines.extend(target_diff_lines)
    if submitted:
        lines.extend(_format_order_lines(submitted, dry_run_only=dry_run_only, translator=translator))
    elif skipped and has_rebalance_attempt:
        reason = _format_skipped_reason(skipped, translator=translator)
        lines.append(translator("no_order_submitted", reason=reason))
    else:
        lines.append(translator("no_rebalance_needed"))
    return "\n".join(str(line) for line in lines if str(line).strip())
