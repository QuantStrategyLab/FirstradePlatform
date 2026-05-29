"""Small Telegram notification helpers for FirstradePlatform."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from quant_platform_kit.common.notification_localization import (
    localize_notification_text as _base_localize_notification_text,
)

try:
    from quant_platform_kit.common.notification_localization import (
        localize_price_source_label as _localize_price_source_label,
        localize_quote_overlay_state as _localize_quote_overlay_state,
    )
except ImportError:  # pragma: no cover - compatibility with older pinned shared wheels
    _PRICE_SOURCE_LABELS = {
        "longbridge_candlesticks": ("LongBridge 日线K线", "LongBridge daily candlesticks"),
        "schwab_daily_history_with_live_quote_overlay": (
            "Schwab 日线历史 + 实时报价覆盖",
            "Schwab daily history + live quote overlay",
        ),
        "firstrade_ohlc_with_live_quote_overlay": (
            "Firstrade OHLC + 实时报价覆盖",
            "Firstrade OHLC + live quote overlay",
        ),
        "market_quote": ("实时行情报价", "market quote"),
        "mixed_market_quote_snapshot_close": (
            "实时行情报价 + 快照收盘价回补",
            "market quote + snapshot close fallback",
        ),
        "mixed_market_quote_historical_close": (
            "实时行情报价 + 历史收盘价回补",
            "market quote + historical close fallback",
        ),
        "snapshot_close": ("快照收盘价", "snapshot close"),
        "historical_close": ("历史收盘价", "historical close"),
        "market_data": ("市场数据", "market data"),
    }

    def _locale_uses_zh(locale: str | None) -> bool:
        return str(locale or "").strip().lower().startswith("zh")

    def _localize_price_source_label(value, *, translator=None, locale=None):
        source = str(value or "").strip()
        use_zh = _locale_uses_zh(locale)
        if not source:
            return "未知" if use_zh else "unknown"
        label = _PRICE_SOURCE_LABELS.get(source)
        if label is not None:
            return label[0] if use_zh else label[1]
        return source.replace("_", " ")

    def _localize_quote_overlay_state(value, *, translator=None, locale=None):
        use_zh = _locale_uses_zh(locale)
        if value is True:
            return "是" if use_zh else "yes"
        if value is False:
            return "否" if use_zh else "no"
        return "未知" if use_zh else "unknown"
try:
    from quant_platform_kit.common.small_account_compatibility import (
        format_small_account_cash_substitution_notes,
    )
except ImportError:  # pragma: no cover - compatibility with older pinned shared wheels
    def format_small_account_cash_substitution_notes(
        notes,
        *,
        translator,
        wrapper_key="buy_deferred",
        detail_key="buy_deferred_small_account_cash_substitution",
        cash_label_key="cash_label",
        symbol_suffix=".US",
    ):
        messages = []
        seen_keys = set()
        for note in tuple(notes or ()):
            if not isinstance(note, Mapping):
                continue
            symbol = str(note.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            target_value = max(0.0, float(note.get("target_value") or 0.0))
            price = max(0.0, float(note.get("price") or 0.0))
            if target_value <= 0.0 or price <= 0.0:
                continue
            cash_symbols = tuple(
                dict.fromkeys(
                    str(cash_symbol or "").strip().upper()
                    for cash_symbol in tuple(note.get("cash_symbols") or ())
                    if str(cash_symbol or "").strip()
                )
            )
            cash_symbols_text = ", ".join(f"{cash_symbol}{symbol_suffix}" for cash_symbol in cash_symbols)
            if not cash_symbols_text:
                cash_symbols_text = translator(cash_label_key)
            note_key = (symbol, f"{target_value:.2f}", cash_symbols_text)
            if note_key in seen_keys:
                continue
            seen_keys.add(note_key)
            detail = translator(
                detail_key,
                symbol=f"{symbol}{symbol_suffix}",
                diff=f"{target_value:.2f}",
                price=f"{price:.2f}",
                cash_symbols=cash_symbols_text,
            )
            messages.append(translator(wrapper_key, detail=detail))
        return tuple(messages)


SEPARATOR = "━━━━━━━━━━━━━━━━━━"

_DETAIL_FIELD_SPLIT_RE = re.compile(r",\s*(?=[A-Za-z_][\w-]*\s*=)")
_STRUCTURED_PAREN_RE = re.compile(r"^(?P<key>[A-Za-z_][\w-]*)\((?P<details>.*)\)$")


I18N = {
    "zh": {
        "rebalance_title": "🔔 【调仓指令】",
        "heartbeat_title": "💓 【心跳检测】",
        "strategy_label": "🧭 策略: {name}",
        "account_label": "🆔 账户: {account}",
        "dry_run_banner": "🧪 模拟运行，本轮不提交真实订单",
        "dashboard_label": "📊 资产看板",
        "account_overview_title": "📌 策略账户概览",
        "equity": "净值",
        "total_assets": "总资产（策略标的+现金）",
        "buying_power": "购买力",
        "reserved_cash": "预留现金",
        "investable_cash": "可投资现金",
        "cash_label": "现金",
        "holdings_title": "💼 策略持仓",
        "holding_line": "{symbol}: {market_value} / {quantity}",
        "quantity_share": "{quantity}股",
        "quantity_shares": "{quantity}股",
        "signal_label": "信号",
        "strategy_plugin_line": "🧩 插件：{plugin} | 状态：{route} | 提醒：{action}",
        "strategy_plugin_alert_subject": "🚨 策略插件告警：{plugin} | {route}",
        "strategy_plugin_alert_title": "🚨 【策略插件告警】",
        "strategy_plugin_alert_context": "运行环境：{context}",
        "strategy_plugin_alert_strategy": "策略：{strategy}",
        "strategy_plugin_alert_plugin": "插件：{plugin}",
        "strategy_plugin_alert_status": "状态：{route}",
        "strategy_plugin_alert_action": "人工处理建议：{action}",
        "strategy_plugin_alert_mode": "模式：{mode}",
        "strategy_plugin_alert_as_of": "信号时间：{as_of}",
        "strategy_plugin_alert_guidance": "处置建议：{guidance}",
        "strategy_plugin_alert_scope_note": "执行范围：{scope_note}",
        "strategy_plugin_alert_scope": "仅作人工复核提醒；插件不会自动下单或改仓位",
        "strategy_plugin_name_crisis_response_shadow": "危机观察通知",
        "strategy_plugin_name_taco_rebound_shadow": "TACO 抄底观察通知",
        "strategy_plugin_mode_shadow": "影子观察",
        "strategy_plugin_route_no_action": "未触发",
        "strategy_plugin_route_true_crisis": "真危机",
        "strategy_plugin_route_taco_rebound": "TACO 反弹确认",
        "strategy_plugin_route_unknown_route": "未知状态",
        "strategy_plugin_action_no_action": "不操作",
        "strategy_plugin_action_watch_only": "仅通知",
        "strategy_plugin_action_notify_manual_review": "通知人工复核",
        "strategy_plugin_action_defend": "防守",
        "strategy_plugin_action_blocked": "已阻断",
        "strategy_plugin_guidance_crisis_response_shadow_true_crisis_defend": "优先考虑降低杠杆或清理杠杆仓位，暂停加仓；如需保留风险敞口，先降到可承受的小仓位。",
        "strategy_plugin_guidance_crisis_response_shadow_no_action_blocked": "危机路线被风控阻断；先核对数据新鲜度和外部情境，不建议仅凭此条加仓。",
        "strategy_plugin_guidance_taco_rebound_shadow_taco_rebound_notify_manual_review": "TACO 仅提示可能的反弹窗口；可考虑小仓位、分批、预设止损/失效条件的人工博弈，不建议一次性满仓。",
        "strategy_plugin_action_monitor": "持续观察",
        "strategy_plugin_action_unknown_action": "未知提醒",
        "separator": SEPARATOR,
        "same_trading_day": "当日执行",
        "next_trading_day": "次一交易日执行",
        "next_n_trading_days": "{count}个交易日后执行",
        "timing_line": "⏱ 执行时点: {value}",
        "market_status_line": "📊 市场状态: {status}",
        "signal_line": "🎯 信号: {signal}",
        "target_diff_summary": "调仓变化: {details}",
        "order_logs_title": "🧾 执行明细",
        "dry_run_order": "🧪 模拟{order_type}{side} {symbol}: {quantity}{price}",
        "submitted_order": "{icon} 已提交{order_type}{side} {symbol}: {quantity}{price}{order_id}",
        "order_type_limit": "限价",
        "order_type_market": "市价",
        "side_buy": "买入",
        "side_sell": "卖出",
        "order_price_suffix": " @ ${price}",
        "order_id_suffix": "（订单号: {order_id}）",
        "no_order_submitted": "未下单: 原因={reason}",
        "execution_blocked_banner": "⚠️ 执行阻塞: {reason}",
        "execution_blocked_retryable_banner": "⚠️ 执行阻塞，可在窗口内自动重试: {reason}",
        "funding_blocked_banner": "⚠️ 资金不足，本周期不再自动重试: {reason}",
        "no_rebalance_needed": "✅ 无需调仓",
        "no_trades": "✅ 无需调仓",
        "no_executable_orders": "无可执行订单",
        "buy_deferred": "ℹ️ [买入说明] {detail}",
        "buy_deferred_small_account_cash_substitution": "{symbol} 目标金额 ${diff} 低于 1 股价格 ${price}；为避免超过目标仓位，小账户本轮保留现金，不回补 {cash_symbols}",
        "signal_state_hold": "趋势持有",
        "signal_state_entry": "入场信号",
        "signal_state_reduce": "减仓信号",
        "signal_state_exit": "离场信号",
        "signal_state_idle": "等待信号",
        "signal_hold": "趋势持有",
        "signal_entry": "入场信号",
        "signal_reduce": "减仓信号",
        "signal_exit": "离场信号",
        "signal_idle": "等待信号",
        "market_status_blend_gate_risk_on": "🚀 风险开启（{asset}）",
        "market_status_blend_gate_defensive": "🛡️ 降杠杆（{asset}）",
        "market_status_blend_gate_overlay_capped": "🧯 过热降档（{asset}）",
        "signal_blend_gate_risk_on": "{trend_symbol} 站上 {window} 日门槛线，持有 SOXL {soxl_ratio} + SOXX {soxx_ratio}",
        "signal_blend_gate_defensive": "{trend_symbol} 跌破门槛线，防守持有 SOXX {soxx_ratio}",
        "signal_blend_gate_overlay_capped": "{trend_symbol} 仍在 {window} 日门槛线上方，但触发过热降档（{reasons}），目标仓位 {allocation_text}",
        "market_status_risk_on": "🚀 风险开启（{asset}）",
        "market_status_delever": "🛡️ 降杠杆（{asset}）",
        "signal_risk_on": "SOXL 站上 {window} 日均线，持有 SOXL，交易层风险仓位 {ratio}",
        "signal_delever": "SOXL 跌破 {window} 日均线，切换至 SOXX，交易层风险仓位 {ratio}",
        "blend_gate_reason_rsi_cap": "RSI 超阈值",
        "blend_gate_reason_bollinger_cap": "突破布林上轨",
        "blend_gate_reason_volatility_delever": "{symbol} {window} 日年化波动率 {volatility} 高于 {threshold}，SOXL 转向 {redirect_symbol}",
        "small_account_warning_note": "小账户提示：净值 {portfolio_equity} 低于建议 {min_recommended_equity}；{reason}",
        "small_account_warning_reason_integer_shares_min_position_value_may_prevent_backtest_replication": "整数股和最小仓位限制可能导致实盘无法完全复现回测",
        "strategy_name_tqqq_growth_income": "TQQQ 增长收益",
        "strategy_name_soxl_soxx_trend_income": "SOXL/SOXX 半导体趋势收益",
        "strategy_name_global_etf_rotation": "全球 ETF 轮动",
        "strategy_name_global_etf_confidence_vol_gate": "全球 ETF 置信波动门控",
        "strategy_name_russell_1000_multi_factor_defensive": "罗素1000多因子",
        "strategy_name_tech_communication_pullback_enhancement": "科技通信回调增强",
        "strategy_name_qqq_tech_enhancement": "科技通信回调增强",
        "strategy_name_mega_cap_leader_rotation_top50_balanced": "Mega Cap Top50 平衡龙头轮动",
        "skip_reason_below_trade_threshold": "低于调仓阈值",
        "skip_reason_quote_unavailable": "无法获取报价",
        "skip_reason_sell_quantity_zero": "卖出股数为0",
        "skip_reason_buy_quantity_zero": "买入股数为0",
        "skip_reason_insufficient_cash_for_whole_share": "现金不足以买入一整股",
        "skip_reason_unknown": "未知原因",
    },
    "en": {
        "rebalance_title": "🔔 【Rebalance Instruction】",
        "heartbeat_title": "💓 【Heartbeat】",
        "strategy_label": "🧭 Strategy: {name}",
        "account_label": "🆔 Account: {account}",
        "dry_run_banner": "🧪 Dry run only; no live orders submitted",
        "dashboard_label": "📊 Dashboard",
        "account_overview_title": "📌 Strategy Account",
        "equity": "Equity",
        "total_assets": "Total assets",
        "buying_power": "Buying power",
        "reserved_cash": "Reserved cash",
        "investable_cash": "Investable cash",
        "cash_label": "Cash",
        "holdings_title": "💼 Strategy Holdings",
        "holding_line": "{symbol}: {market_value} / {quantity}",
        "quantity_share": "{quantity} share",
        "quantity_shares": "{quantity} shares",
        "signal_label": "Signal",
        "strategy_plugin_line": "🧩 Plugin: {plugin} | status: {route} | notice: {action}",
        "strategy_plugin_alert_subject": "🚨 Strategy plugin alert: {plugin} | {route}",
        "strategy_plugin_alert_title": "🚨 【Strategy Plugin Alert】",
        "strategy_plugin_alert_context": "Context: {context}",
        "strategy_plugin_alert_strategy": "Strategy: {strategy}",
        "strategy_plugin_alert_plugin": "Plugin: {plugin}",
        "strategy_plugin_alert_status": "Status: {route}",
        "strategy_plugin_alert_action": "Notice: {action}",
        "strategy_plugin_alert_mode": "Mode: {mode}",
        "strategy_plugin_alert_as_of": "Signal as-of: {as_of}",
        "strategy_plugin_alert_guidance": "Manual guidance: {guidance}",
        "strategy_plugin_alert_scope_note": "Execution scope: {scope_note}",
        "strategy_plugin_alert_scope": "Manual review notice only; the plugin does not place orders or change allocations",
        "strategy_plugin_name_crisis_response_shadow": "Crisis Watch Notice",
        "strategy_plugin_name_taco_rebound_shadow": "TACO Rebound Watch Notice",
        "strategy_plugin_mode_shadow": "shadow",
        "strategy_plugin_route_no_action": "no alert",
        "strategy_plugin_route_true_crisis": "true crisis",
        "strategy_plugin_route_taco_rebound": "TACO rebound confirmed",
        "strategy_plugin_route_unknown_route": "unknown status",
        "strategy_plugin_action_no_action": "no action",
        "strategy_plugin_action_watch_only": "notify only",
        "strategy_plugin_action_notify_manual_review": "notify manual review",
        "strategy_plugin_action_defend": "defend",
        "strategy_plugin_action_blocked": "blocked",
        "strategy_plugin_guidance_crisis_response_shadow_true_crisis_defend": "Consider reducing or clearing leveraged exposure, then pause new risk additions; if keeping exposure, resize it to a small amount you can tolerate.",
        "strategy_plugin_guidance_crisis_response_shadow_no_action_blocked": "A guard blocked the crisis route; verify data freshness and external context before acting on this alert.",
        "strategy_plugin_guidance_taco_rebound_shadow_taco_rebound_notify_manual_review": "TACO only flags a possible rebound window; consider a small staged manual probe with a predefined invalidation level instead of full-size exposure.",
        "strategy_plugin_action_monitor": "watch",
        "strategy_plugin_action_unknown_action": "unknown notice",
        "separator": SEPARATOR,
        "same_trading_day": "same trading day",
        "next_trading_day": "next trading day",
        "next_n_trading_days": "next {count} trading days",
        "timing_line": "⏱ Timing: {value}",
        "market_status_line": "📊 Market: {status}",
        "signal_line": "🎯 Signal: {signal}",
        "target_diff_summary": "Target changes: {details}",
        "order_logs_title": "🧾 Execution details",
        "dry_run_order": "🧪 Dry-run {order_type} {side} {symbol}: {quantity}{price}",
        "submitted_order": "{icon} Submitted {order_type} {side} {symbol}: {quantity}{price}{order_id}",
        "order_type_limit": "limit",
        "order_type_market": "market",
        "side_buy": "buy",
        "side_sell": "sell",
        "order_price_suffix": " @ ${price}",
        "order_id_suffix": " (ID: {order_id})",
        "no_order_submitted": "No order submitted: reason={reason}",
        "execution_blocked_banner": "⚠️ Execution blocked: {reason}",
        "execution_blocked_retryable_banner": "⚠️ Execution blocked; retryable within window: {reason}",
        "funding_blocked_banner": "⚠️ Funding blocked; no more automatic retries for this period: {reason}",
        "no_rebalance_needed": "✅ No rebalance needed",
        "no_trades": "✅ No rebalance needed",
        "no_executable_orders": "no executable orders",
        "buy_deferred": "ℹ️ [Buy note] {detail}",
        "buy_deferred_small_account_cash_substitution": "{symbol} target ${diff} is below the 1-share price ${price}; to avoid exceeding the target allocation, this small account keeps cash this cycle and does not rebuy {cash_symbols}",
        "signal_state_hold": "Trend Hold",
        "signal_state_entry": "Entry Signal",
        "signal_state_reduce": "Reduce Signal",
        "signal_state_exit": "Exit Signal",
        "signal_state_idle": "Idle",
        "signal_hold": "Trend Hold",
        "signal_entry": "Entry Signal",
        "signal_reduce": "Reduce Signal",
        "signal_exit": "Exit Signal",
        "signal_idle": "Idle",
        "market_status_blend_gate_risk_on": "Risk on ({asset})",
        "market_status_blend_gate_defensive": "Defensive ({asset})",
        "market_status_blend_gate_overlay_capped": "Overheat capped ({asset})",
        "signal_blend_gate_risk_on": "{trend_symbol} is above the {window}-day gate; hold SOXL {soxl_ratio} + SOXX {soxx_ratio}",
        "signal_blend_gate_defensive": "{trend_symbol} is below the gate; hold SOXX {soxx_ratio}",
        "signal_blend_gate_overlay_capped": "{trend_symbol} remains above the {window}-day gate, but overheat cap is active ({reasons}); target {allocation_text}",
        "market_status_risk_on": "Risk on ({asset})",
        "market_status_delever": "Delever ({asset})",
        "signal_risk_on": "SOXL is above the {window}-day average; hold SOXL at risk sleeve {ratio}",
        "signal_delever": "SOXL is below the {window}-day average; switch to SOXX at risk sleeve {ratio}",
        "blend_gate_reason_rsi_cap": "RSI over threshold",
        "blend_gate_reason_bollinger_cap": "price above upper band",
        "blend_gate_reason_volatility_delever": "{symbol} {window}d annualized volatility {volatility} is above {threshold}; redirect SOXL to {redirect_symbol}",
        "small_account_warning_note": "small account warning: portfolio equity {portfolio_equity} is below recommended {min_recommended_equity}; {reason}",
        "small_account_warning_reason_integer_shares_min_position_value_may_prevent_backtest_replication": "integer-share minimum position sizing may prevent backtest replication",
        "strategy_name_tqqq_growth_income": "TQQQ Growth Income",
        "strategy_name_soxl_soxx_trend_income": "SOXL/SOXX Semiconductor Trend Income",
        "strategy_name_global_etf_rotation": "Global ETF Rotation",
        "strategy_name_global_etf_confidence_vol_gate": "Global ETF Confidence Vol Gate",
        "strategy_name_russell_1000_multi_factor_defensive": "Russell 1000 Multi-Factor Defensive",
        "strategy_name_tech_communication_pullback_enhancement": "Tech Communication Pullback Enhancement",
        "strategy_name_qqq_tech_enhancement": "Tech Communication Pullback Enhancement",
        "strategy_name_mega_cap_leader_rotation_top50_balanced": "Mega Cap Top50 Balanced Leader Rotation",
        "skip_reason_below_trade_threshold": "below trade threshold",
        "skip_reason_quote_unavailable": "quote unavailable",
        "skip_reason_sell_quantity_zero": "sell quantity rounds to 0",
        "skip_reason_buy_quantity_zero": "buy quantity rounds to 0",
        "skip_reason_insufficient_cash_for_whole_share": "insufficient cash for one whole share",
        "skip_reason_unknown": "unknown reason",
    },
}


def build_translator(lang: str | None) -> Callable[..., str]:
    normalized = str(lang or "").lower()
    active_lang = "zh" if normalized.startswith("zh") else "en"

    def translate(key: str, **kwargs) -> str:
        template = I18N[active_lang].get(key, I18N["en"].get(key, key))
        if not kwargs:
            return template
        try:
            return template.format(**kwargs)
        except (IndexError, KeyError, ValueError):
            return template

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


def _format_price(value: Any) -> str:
    number = _safe_float(value)
    return "" if number is None else f"{number:,.2f}"


def _format_quantity(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "0"
    if float(number).is_integer():
        return str(int(number))
    return f"{number:g}"


def _format_shares(value: Any, *, translator: Callable[..., str]) -> str:
    quantity = _format_quantity(value)
    key = "quantity_share" if quantity == "1" else "quantity_shares"
    return translator(key, quantity=quantity)


def _parse_detail_kwargs(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in _DETAIL_FIELD_SPLIT_RE.split(str(text or "")):
        key, sep, value = part.partition("=")
        if not sep:
            continue
        normalized_key = key.strip()
        if not normalized_key:
            continue
        values[normalized_key] = value.strip()
    return values


def _structured_key_and_kwargs(text: str) -> tuple[str, dict[str, str]]:
    key, sep, details = text.partition(":")
    if sep:
        return key.strip(), _parse_detail_kwargs(details)
    match = _STRUCTURED_PAREN_RE.fullmatch(text.strip())
    if not match:
        return "", {}
    return match.group("key").strip(), _parse_detail_kwargs(match.group("details"))


def _localize_structured_text(text: Any, *, translator: Callable[..., str]) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    translation_key, kwargs = _structured_key_and_kwargs(value)
    if translation_key:
        for nested_key in ("reason", "reasons"):
            if nested_key in kwargs:
                kwargs[nested_key] = _base_localize_notification_text(
                    kwargs[nested_key],
                    translator=translator,
                )
        translated = translator(translation_key, **kwargs) if kwargs else translator(translation_key)
        if translated != translation_key:
            return translated
    translated = translator(value)
    if translated != value:
        return translated
    return _base_localize_notification_text(value, translator=translator)


def _is_dashboard_signal_line(line: str) -> bool:
    text = str(line or "").strip()
    text = text.removeprefix("-").strip()
    if not text:
        return False
    lowered = text.lower()
    return (
        text.startswith("🎯")
        or lowered.startswith(("signal:", "signal："))
        or text.startswith(("信号:", "信号："))
        or "small account warning" in lowered
        or "小账户提示" in text
    )


def _format_dashboard_lines(
    portfolio: Mapping[str, Any],
    execution: Mapping[str, Any],
    *,
    translator: Callable[..., str],
) -> list[str]:
    dashboard_text = str(execution.get("dashboard_text") or "").strip()
    if dashboard_text:
        has_signal_display = bool(str(execution.get("signal_display") or "").strip())
        lines = []
        for line in dashboard_text.splitlines():
            if not line.strip():
                continue
            if has_signal_display and _is_dashboard_signal_line(line):
                continue
            lines.append(_base_localize_notification_text(line.rstrip(), translator=translator))
        return lines

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


def _infer_quote_overlay_used(source: str, overlay):
    if overlay is not None:
        return overlay
    normalized_source = str(source or "").strip().lower()
    if "with_live_quote_overlay" in normalized_source:
        return True
    if normalized_source in {
        "longbridge_candlesticks",
        "historical_close",
        "snapshot_close",
        "market_quote",
    }:
        return False
    return None


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


def _format_signal_snapshot_line(snapshot: Any, *, lang: str) -> str:
    if not isinstance(snapshot, Mapping):
        return ""
    market_date = str(snapshot.get("market_date") or snapshot.get("signal_as_of") or "").strip()
    source = str(snapshot.get("latest_price_source") or "").strip()
    overlay = _infer_quote_overlay_used(source, snapshot.get("quote_overlay_used"))
    warning = snapshot.get("data_freshness_warning")
    if not market_date and not source and overlay is None and warning in (None, "", False):
        return ""
    use_zh = str(lang or "").lower().startswith("zh")
    if use_zh:
        parts = [
            f"日期 {market_date or '未知'}",
            f"数据源 {_localize_price_source_label(source, locale=lang)}",
            f"报价覆盖 {_localize_quote_overlay_state(overlay, locale=lang)}",
        ]
        if warning not in (None, "", False):
            parts.append(f"提示 {_base_localize_notification_text(warning, translator=build_translator(lang))}")
        return "🧾 信号快照: " + " | ".join(parts)
    parts = [
        f"date {market_date or 'unknown'}",
        f"source {_localize_price_source_label(source, locale=lang)}",
        f"quote overlay {_localize_quote_overlay_state(overlay, locale=lang)}",
    ]
    if warning not in (None, "", False):
        parts.append(f"warning {warning}")
    return "🧾 Signal snapshot: " + " | ".join(parts)


def _first_summary(value: Any, *, translator: Callable[..., str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    summary = text.split(" | ", 1)[0].strip()
    structured = _localize_structured_text(summary, translator=translator)
    if structured and structured != summary:
        return structured
    normalized = summary.lower()
    for key in (f"signal_state_{normalized}", f"signal_{normalized}"):
        translated = translator(key)
        if translated != key:
            return translated
    return structured or _base_localize_notification_text(summary, translator=translator)


def _detail_lines(value: Any, *, translator: Callable[..., str]) -> list[str]:
    segments = [segment.strip() for segment in str(value or "").split(" | ") if segment.strip()]
    details = []
    for segment in segments[1:]:
        localized = _localize_structured_text(segment, translator=translator)
        if localized:
            details.append(localized)
    return details


def _format_signal_lines(execution: Mapping[str, Any], *, translator: Callable[..., str]) -> list[str]:
    status = _first_summary(execution.get("status_display"), translator=translator)
    signal = _first_summary(execution.get("signal_display"), translator=translator)
    lines = []
    if status and status != signal:
        lines.append(translator("market_status_line", status=status))
    if signal:
        lines.append(translator("signal_line", signal=signal))
        lines.extend(f"  - {line}" for line in _detail_lines(execution.get("signal_display"), translator=translator))
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
        raw_payload = dict(order.get("raw_payload") or {})
        order_type = str(order.get("order_type") or raw_payload.get("price_type") or "limit").lower()
        if order_type not in {"limit", "market"}:
            order_type = "limit"
        price = _format_price(order.get("limit_price") or raw_payload.get("limit_price") or raw_payload.get("price"))
        price_suffix = translator("order_price_suffix", price=price) if price else ""
        side_key = "side_buy" if side == "buy" else "side_sell"
        order_type_key = "order_type_limit" if order_type == "limit" else "order_type_market"
        quantity = _format_shares(order.get("quantity"), translator=translator)
        if dry_run_only:
            lines.append(
                translator(
                    "dry_run_order",
                    order_type=translator(order_type_key),
                    side=translator(side_key),
                    symbol=symbol,
                    quantity=quantity,
                    price=price_suffix,
                )
            )
            continue
        order_id = str(order.get("broker_order_id") or raw_payload.get("order_id") or "").strip()
        order_id_suffix = translator("order_id_suffix", order_id=order_id) if order_id else ""
        lines.append(
            translator(
                "submitted_order",
                icon="📈" if side == "buy" else "📉",
                order_type=translator(order_type_key),
                side=translator(side_key),
                symbol=symbol,
                quantity=quantity,
                price=price_suffix,
                order_id=order_id_suffix,
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
    strategy_profile = str(result.get("strategy_profile") or "").strip()
    strategy_name = str(result.get("strategy_display_name") or strategy_profile).strip()
    translated_strategy_name = translator(f"strategy_name_{strategy_profile}") if strategy_profile else ""
    if translated_strategy_name and translated_strategy_name != f"strategy_name_{strategy_profile}":
        strategy_name = translated_strategy_name
    account = str(result.get("account") or "").strip()
    lines = [translator("rebalance_title" if has_rebalance_attempt else "heartbeat_title")]
    if strategy_name:
        lines.append(translator("strategy_label", name=strategy_name))
    if account:
        lines.append(translator("account_label", account=account))
    if dry_run_only:
        lines.append(translator("dry_run_banner"))
    if bool(result.get("execution_blocked")):
        blocked = list(result.get("execution_blocking_skips") or skipped)
        reason = _format_skipped_reason(blocked, translator=translator)
        if bool(result.get("funding_blocked")):
            banner_key = "funding_blocked_banner"
        elif result.get("execution_block_retryable") is True:
            banner_key = "execution_blocked_retryable_banner"
        else:
            banner_key = "execution_blocked_banner"
        lines.append(translator(banner_key, reason=reason))

    dashboard_lines = _format_dashboard_lines(portfolio, execution, translator=translator)
    if dashboard_lines:
        lines.append(SEPARATOR)
        lines.extend(dashboard_lines)
    lines.extend(_format_timing_lines(execution, translator=translator))
    signal_snapshot_line = _format_signal_snapshot_line(result.get("signal_snapshot"), lang=lang)
    if signal_snapshot_line:
        lines.append(signal_snapshot_line)
    lines.extend(_format_signal_lines(execution, translator=translator))
    lines.append(SEPARATOR)
    lines.extend(target_diff_lines)
    execution_notes = tuple(result.get("execution_notes") or allocation.get("small_account_whole_share_cash_notes") or ())
    lines.extend(format_small_account_cash_substitution_notes(execution_notes, translator=translator))
    if submitted:
        lines.append(translator("order_logs_title"))
        lines.extend(_format_order_lines(submitted, dry_run_only=dry_run_only, translator=translator))
    elif skipped and has_rebalance_attempt:
        lines.append(translator("order_logs_title"))
        reason = _format_skipped_reason(skipped, translator=translator)
        lines.append(translator("no_order_submitted", reason=reason))
    else:
        lines.append(translator("no_rebalance_needed"))
    return "\n".join(str(line) for line in lines if str(line).strip())
