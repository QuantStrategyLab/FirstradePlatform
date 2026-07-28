"""Small Telegram notification helpers for FirstradePlatform."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from quant_platform_kit.common.notification_localization import (
    localize_notification_text as _base_localize_notification_text,
)
from quant_platform_kit.notifications.renderer_base import (
    as_float_or_none as _as_float_or_none,
    build_tqqq_risk_control_lines as _build_tqqq_risk_control_lines_shared,
    effective_volatility_delever_threshold as _effective_volatility_delever_threshold,
    format_percent as _format_percent,
    format_percentile as _format_percentile,
    format_sample_count as _format_sample_count,
    format_tqqq_volatility_delever_allocation_detail as _format_tqqq_volatility_delever_allocation_detail,
    format_volatility_delever_threshold_detail as _format_volatility_delever_threshold_detail,
    is_truthy,
    localize_price_source_label as _localize_price_source_label,
    present as _present,
)

try:
    from quant_platform_kit.common.notification_localization import (
        merge_strategy_plugin_i18n as _merge_strategy_plugin_i18n,
    )
except ImportError:  # pragma: no cover - compatibility with older pinned shared wheels
    _merge_strategy_plugin_i18n = None

_TELEGRAM_MARKET_SYMBOL_LINK_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z0-9]{1,12})\.([A-Z]{2,4})(?![A-Za-z0-9_])")
_TELEGRAM_MARKET_SYMBOL_LINK_JOINER = "\u2060"


def _break_telegram_market_symbol_auto_links(value: object) -> str:
    text = str(value or "")
    return _TELEGRAM_MARKET_SYMBOL_LINK_RE.sub(
        lambda match: f"{match.group(1)}.{_TELEGRAM_MARKET_SYMBOL_LINK_JOINER}{match.group(2)}",
        text,
    )


try:
    from quant_platform_kit.common.small_account_compatibility import (
        format_small_account_allocation_drift_notes,
        format_small_account_cash_substitution_notes,
    )
except ImportError:  # pragma: no cover - compatibility with older pinned shared wheels
    def format_small_account_allocation_drift_notes(_notes, *, translator, **_kwargs):
        return ()

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


def _format_symbol_with_suffix(symbol, *, suffix=".US") -> str:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return normalized
    if "." in normalized:
        return normalized
    normalized_suffix = str(suffix or "").strip().upper()
    return f"{normalized}{normalized_suffix}" if normalized_suffix else normalized


def format_small_account_whole_share_bootstrap_notes(
    symbols,
    *,
    translator,
    symbol_suffix=".US",
) -> tuple[str, ...]:
    normalized_symbols = tuple(
        dict.fromkeys(
            _format_symbol_with_suffix(symbol, suffix=symbol_suffix)
            for symbol in tuple(symbols or ())
            if str(symbol or "").strip()
        )
    )
    if not normalized_symbols:
        return ()
    try:
        message = translator(
            "buy_lifted_small_account_whole_share",
            symbols=", ".join(normalized_symbols),
        )
    except Exception:
        message = ""
    if not message or message == "buy_lifted_small_account_whole_share":
        message = (
            f"ℹ️ [买入说明] {', '.join(normalized_symbols)} 目标金额接近 1 股；"
            "小账户整数股兼容，本轮允许按 1 股下单"
        )
    return (message,)


SEPARATOR = "━━━━━━━━━━━━━━━━━━"

_DETAIL_FIELD_SPLIT_RE = re.compile(r",\s*(?=[A-Za-z_][\w-]*\s*=)")
_STRUCTURED_PAREN_RE = re.compile(r"^(?P<key>[A-Za-z_][\w-]*)\((?P<details>.*)\)$")
_DASHBOARD_POSITION_LINE_RE = re.compile(r"^[A-Z][A-Z0-9./-]{0,12}\s*:")


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
        "total_assets_margin": "总资产（策略净值）",
        "buying_power": "可用现金",
        "buying_power_margin": "购买力",
        "reserved_cash": "预留现金",
        "investable_cash": "可投资现金",
        "cash_label": "现金",
        "holdings_title": "💼 策略持仓",
        "holding_line": "{symbol}: {market_value} / {quantity}",
        "quantity_share": "{quantity}股",
        "quantity_shares": "{quantity}股",
        "signal_label": "信号",
        "strategy_plugin_line": "🧩 插件：{plugin} | 启用：{enabled} | 状态：{route} | 提醒：{action}",
        "strategy_plugin_enabled_true": "是",
        "strategy_plugin_enabled_false": "否",
        "strategy_plugin_consumption_auto": "🧩 插件本次影响：已按策略规则参与本轮仓位计算",
        "strategy_plugin_consumption_auto_defend": "🧩 插件本次影响：已触发防守规则并参与本轮仓位计算",
        "strategy_plugin_consumption_auto_delever": "🧩 插件本次影响：已触发降杠杆规则并参与本轮仓位计算",
        "strategy_plugin_consumption_loaded_not_applied": "🧩 插件本次影响：已加载；该状态未启用自动仓位改写",
        "strategy_plugin_consumption_review_only": "🧩 插件本次影响：仅通知复核；当前状态未触发自动仓位改写",
        "strategy_plugin_consumption_unavailable": "🧩 插件本次影响：未加载可用插件信号",

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
        "strategy_plugin_name_macro_risk_governor": "宏观风险控制通知",
        "strategy_plugin_name_market_regime_control": "市场状态控制",
        "strategy_plugin_name_panic_reversal_shadow": "恐慌反转观察通知",
        "strategy_plugin_name_taco_rebound_shadow": "TACO 反弹观察通知",
        "strategy_plugin_mode_shadow": "影子观察",
        "strategy_plugin_route_blocked": "已阻断",
        "strategy_plugin_route_crisis": "危机",
        "strategy_plugin_route_delever": "降杠杆",
        "strategy_plugin_route_no_action": "未触发",
        "strategy_plugin_route_opportunity_watch": "机会观察",
        "strategy_plugin_route_panic_reversal": "恐慌反转",
        "strategy_plugin_route_risk_off": "风险关闭",
        "strategy_plugin_route_risk_reduced": "风险降低",
        "strategy_plugin_route_true_crisis": "真危机",
        "strategy_plugin_route_taco_rebound": "TACO 反弹确认",
        "strategy_plugin_route_unknown_route": "未知状态",
        "strategy_plugin_route_watch": "观察",
        "strategy_plugin_action_no_action": "不操作",
        "strategy_plugin_action_watch_only": "仅通知",
        "strategy_plugin_action_notify_manual_review": "通知人工复核",
        "strategy_plugin_action_defend": "防守",
        "strategy_plugin_action_delever": "降杠杆",
        "strategy_plugin_action_blocked": "已阻断",
        "strategy_plugin_guidance_crisis_response_shadow_true_crisis_defend": "优先考虑降低杠杆或清理杠杆仓位，暂停加仓；如需保留风险敞口，先降到可承受的小仓位。",
        "strategy_plugin_guidance_crisis_response_shadow_no_action_blocked": "危机路线被风控阻断；先核对数据新鲜度和外部情境，不建议仅凭此条加仓。",
        "strategy_plugin_guidance_macro_risk_governor_delever_delever": "宏观风险控制建议降低杠杆敞口；是否执行由策略侧可回测规则和仓位适配器决定。",
        "strategy_plugin_guidance_macro_risk_governor_crisis_defend": "宏观危机信号建议风险仓位转向防守或现金类资产，直到压力缓和。",
        "strategy_plugin_guidance_market_regime_control_risk_off_defend": "市场状态控制进入风险关闭；机会类信号先不执行，风险仓位应保持防守。",
        "strategy_plugin_guidance_market_regime_control_risk_reduced_delever": "市场状态控制建议降杠杆；自动仓位调整只按策略侧已批准的可回测规则执行。",
        "strategy_plugin_guidance_market_regime_control_opportunity_watch_notify_manual_review": "仅作人工复核：市场状态允许有限机会观察，但插件本身不会下单或直接改仓位。",
        "strategy_plugin_guidance_market_regime_control_blocked_blocked": "市场状态控制被数据质量或新鲜度保护阻断；先核对数据源和产物，再决定是否人工处理。",
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
        "submitted_order": "{icon} 已提交{order_type}{side} {symbol}: {quantity}{price}{order_id}（尚未确认成交；限价单可能未成交或取消）",
        "submitted_limit_order": "{icon} 已提交{order_type}{side} {symbol}: {quantity}{price}{order_id}（尚未确认成交；限价单可能未成交或取消）",
        "submitted_market_order": "{icon} 已提交{order_type}{side} {symbol}: {quantity}{price}{order_id}（券商已受理，尚未确认成交）",
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
        "small_account_allocation_drift": "📏 整数股偏离：若本轮订单全部成交，{details}",
        "small_account_allocation_drift_detail": "{symbol} 预计 {projected_weight} vs 目标 {target_weight}（{drift_weight}）",
        "buy_lifted_small_account_whole_share": "ℹ️ [买入说明] {symbols} 目标金额接近 1 股；小账户整数股兼容，本轮允许按 1 股下单",
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
        "market_status_blend_gate_overlay_capped": "🧯 风控降档（{asset}）",
        "signal_blend_gate_risk_on": "{trend_symbol} 站上 {window} 日门槛线，持有 SOXL {soxl_ratio} + SOXX {soxx_ratio}",
        "signal_blend_gate_defensive": "{trend_symbol} 跌破门槛线，防守持有 SOXX {soxx_ratio}",
        "signal_blend_gate_overlay_capped": "{trend_symbol} 仍在 {window} 日门槛线上方，但触发风控降档（{reasons}），目标仓位 {allocation_text}",
        "risk_control_tqqq_volatility_delever_applied": "🛡️ 风控: QQQ {window} 日年化波动率 {volatility} 高于 {threshold}，{source_symbol} 转向 {redirect_symbol}（{allocation_detail}）",
        "risk_control_tqqq_volatility_delever_applied_dynamic": "🛡️ 风控: QQQ {window} 日年化波动率 {volatility} 高于实际阈值 {threshold}（{threshold_detail}），{source_symbol} 转向 {redirect_symbol}（{allocation_detail}）",
        "risk_control_tqqq_volatility_delever_hysteresis": "🛡️ 风控: QQQ {window} 日年化波动率 {volatility} 仍高于退出阈值 {exit_threshold}，维持 {source_symbol} 转向 {redirect_symbol}（{allocation_detail}）",
        "risk_control_tqqq_volatility_delever_hysteresis_dynamic": "🛡️ 风控: QQQ {window} 日年化波动率 {volatility} 仍高于退出阈值 {exit_threshold}；入场实际阈值 {threshold}（{threshold_detail}），维持 {source_symbol} 转向 {redirect_symbol}（{allocation_detail}）",
        "tqqq_volatility_delever_allocation_detail": "杠杆仓位：TQQQ 保留 {retained_ratio}，{redirect_symbol} {redirected_ratio}",
        "tqqq_signal_reason_entry_trend": "原因：QQQ 高于 MA200，MA20 斜率为正",
        "tqqq_signal_reason_entry_pullback": "原因：QQQ 低于 MA200，但站上 MA20 且回撤反弹确认",
        "tqqq_signal_reason_hold_trend": "原因：已持有风险仓位，QQQ 仍高于 MA200",
        "tqqq_signal_reason_exit_ma200": "原因：QQQ 跌破 MA200 退出线",
        "tqqq_signal_reason_idle_waiting": "原因：等待 QQQ 站上 MA200 且 MA20 斜率转正",
        "tqqq_signal_reason_macro_delever": "原因：宏观风控降低杠杆",
        "tqqq_signal_reason_macro_defense": "原因：宏观风控转入防守",
        "tqqq_signal_reason_crisis_defense": "原因：危机防御转入避险仓位",
        "market_status_risk_on": "🚀 风险开启（{asset}）",
        "market_status_delever": "🛡️ 降杠杆（{asset}）",
        "signal_risk_on": "SOXL 站上 {window} 日均线，持有 SOXL，交易层风险仓位 {ratio}",
        "signal_delever": "SOXL 跌破 {window} 日均线，切换至 SOXX，交易层风险仓位 {ratio}",
        "blend_gate_reason_rsi_cap": "RSI 超阈值",
        "blend_gate_reason_bollinger_cap": "突破布林上轨",
        "blend_gate_reason_volatility_delever": "{symbol} {window} 日年化波动率 {volatility} 高于 {threshold}，SOXL 转向 {redirect_symbol}",
        "blend_gate_reason_volatility_delever_dynamic": "{symbol} {window} 日年化波动率 {volatility} 高于实际阈值 {threshold}（{threshold_detail}），SOXL 转向 {redirect_symbol}",
        "blend_gate_volatility_threshold_detail_dynamic": "动态 {percentile}，{lookback}日窗口，范围 {floor}-{cap}，样本 {sample_count}",
        "blend_gate_volatility_threshold_detail_dynamic_fallback": "动态样本不足，回退固定 {fixed_threshold}（样本 {sample_count}/{min_periods}，{percentile}）",
        "blend_gate_volatility_threshold_detail_fixed": "固定阈值 {threshold}",
        "small_account_warning_note": "小账户提示：净值 {portfolio_equity} 低于建议 {min_recommended_equity}；{reason}",
        "small_account_warning_reason_integer_shares_min_position_value_may_prevent_backtest_replication": "整数股和最小仓位限制可能导致实盘无法完全复现回测",
        "strategy_name_tqqq_growth_income": "TQQQ 增长收益",
        "strategy_name_soxl_soxx_trend_income": "SOXL/SOXX 半导体趋势收益",
        "strategy_name_global_etf_rotation": "全球 ETF 轮动",
        "strategy_name_global_etf_confidence_vol_gate": "全球 ETF 置信波动门控",
        "strategy_name_tech_communication_pullback_enhancement": "科技通信回调增强",
        "strategy_name_qqq_tech_enhancement": "科技通信回调增强",
        "strategy_name_russell_top50_leader_rotation": "罗素 Top50 领涨轮动",
        "strategy_name_nasdaq_sp500_smart_dca": "纳指100 / 标普500 智能定投",
        "strategy_name_ibit_smart_dca": "IBIT 比特币 ETF 智能定投",
        "skip_reason_below_trade_threshold": "低于调仓阈值",
        "skip_reason_quote_unavailable": "无法获取报价",
        "skip_reason_sell_quantity_zero": "整数股不足 1 股，无需下单",
        "skip_reason_pending_sell_release": "需先减仓但整数股卖单未成交，现金不足以对应买入，跳过以避免融资",
        "skip_reason_negative_cash": "账户现金为负，跳过买入以避免额外融资",
        "skip_reason_buy_quantity_zero": "整数股不足 1 股，无需下单",
        "skip_reason_insufficient_cash_for_whole_share": "现金不足以买入一整股",
        "skip_reason_broker_rejected": "券商拒绝订单",
        "skip_reason_fractional_trading_disclosure_required": "请先在 Firstrade 接受零碎股交易披露（券商拒单 1219）",
        "skip_reason_unknown": "未知原因",
        "deferred_orders_line": "ℹ️ [本轮跳过] {details}",
        "skip_symbols_reason": "{symbols}（{reason}）",
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
        "total_assets_margin": "Total assets (strategy net liquidation)",
        "buying_power": "Available cash",
        "buying_power_margin": "Buying power",
        "reserved_cash": "Reserved cash",
        "investable_cash": "Investable cash",
        "cash_label": "Cash",
        "holdings_title": "💼 Strategy Holdings",
        "holding_line": "{symbol}: {market_value} / {quantity}",
        "quantity_share": "{quantity} share",
        "quantity_shares": "{quantity} shares",
        "signal_label": "Signal",
        "strategy_plugin_line": "🧩 Plugin: {plugin} | enabled: {enabled} | status: {route} | notice: {action}",
        "strategy_plugin_enabled_true": "yes",
        "strategy_plugin_enabled_false": "no",
        "strategy_plugin_consumption_auto": "🧩 Plugin impact this run: included in this cycle's position calculation under strategy rules",
        "strategy_plugin_consumption_auto_defend": "🧩 Plugin impact this run: triggered defensive rules and joined this cycle's position calculation",
        "strategy_plugin_consumption_auto_delever": "🧩 Plugin impact this run: triggered de-levering rules and joined this cycle's position calculation",
        "strategy_plugin_consumption_loaded_not_applied": "🧩 Plugin impact this run: loaded; this state did not enable automatic position rewrites",
        "strategy_plugin_consumption_review_only": "🧩 Plugin impact this run: review-only notice; current state did not trigger automatic position rewrites",
        "strategy_plugin_consumption_unavailable": "🧩 Plugin impact this run: no usable plugin signal loaded",

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
        "strategy_plugin_name_macro_risk_governor": "Macro Risk Governor Notice",
        "strategy_plugin_name_market_regime_control": "Market Regime Control",
        "strategy_plugin_name_panic_reversal_shadow": "Panic Reversal Watch Notice",
        "strategy_plugin_name_taco_rebound_shadow": "TACO Rebound Watch Notice",
        "strategy_plugin_mode_shadow": "shadow",
        "strategy_plugin_route_blocked": "blocked",
        "strategy_plugin_route_crisis": "crisis",
        "strategy_plugin_route_delever": "de-lever",
        "strategy_plugin_route_no_action": "no alert",
        "strategy_plugin_route_opportunity_watch": "opportunity watch",
        "strategy_plugin_route_panic_reversal": "panic reversal",
        "strategy_plugin_route_risk_off": "risk off",
        "strategy_plugin_route_risk_reduced": "risk reduced",
        "strategy_plugin_route_true_crisis": "true crisis",
        "strategy_plugin_route_taco_rebound": "TACO rebound confirmed",
        "strategy_plugin_route_unknown_route": "unknown status",
        "strategy_plugin_route_watch": "watch",
        "strategy_plugin_action_no_action": "no action",
        "strategy_plugin_action_watch_only": "notify only",
        "strategy_plugin_action_notify_manual_review": "notify manual review",
        "strategy_plugin_action_defend": "defend",
        "strategy_plugin_action_delever": "de-lever",
        "strategy_plugin_action_blocked": "blocked",
        "strategy_plugin_guidance_crisis_response_shadow_true_crisis_defend": "Consider reducing or clearing leveraged exposure, then pause new risk additions; if keeping exposure, resize it to a small amount you can tolerate.",
        "strategy_plugin_guidance_crisis_response_shadow_no_action_blocked": "A guard blocked the crisis route; verify data freshness and external context before acting on this alert.",
        "strategy_plugin_guidance_macro_risk_governor_delever_delever": "The macro risk governor suggests reducing leveraged exposure; execution is controlled by strategy-side backtestable rules and position adapters.",
        "strategy_plugin_guidance_macro_risk_governor_crisis_defend": "The macro crisis signal suggests moving the risk sleeve toward defensive or cash-like exposure until stress de-escalates.",
        "strategy_plugin_guidance_market_regime_control_risk_off_defend": "Market regime control is risk-off; opportunity signals should stay blocked and risk exposure should remain defensive.",
        "strategy_plugin_guidance_market_regime_control_risk_reduced_delever": "Market regime control suggests de-levering; automatic position changes only follow strategy-side approved, backtestable rules.",
        "strategy_plugin_guidance_market_regime_control_opportunity_watch_notify_manual_review": "Manual review only: the market regime allows bounded opportunity watch, but the plugin does not place orders or directly change allocations.",
        "strategy_plugin_guidance_market_regime_control_blocked_blocked": "Market regime control was blocked by data-quality or freshness guards; verify source data and artifacts before manual action.",
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
        "submitted_order": "{icon} Submitted {order_type} {side} {symbol}: {quantity}{price}{order_id} (fill not confirmed; a limit order may remain unfilled or be canceled)",
        "submitted_limit_order": "{icon} Submitted {order_type} {side} {symbol}: {quantity}{price}{order_id} (fill not confirmed; a limit order may remain unfilled or be canceled)",
        "submitted_market_order": "{icon} Submitted {order_type} {side} {symbol}: {quantity}{price}{order_id} (accepted by broker; fill not confirmed)",
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
        "small_account_allocation_drift": "📏 Integer-share drift: if this cycle's orders fully fill, {details}",
        "small_account_allocation_drift_detail": "{symbol} projected {projected_weight} vs target {target_weight} ({drift_weight})",
        "buy_lifted_small_account_whole_share": "ℹ️ [Buy note] {symbols} target is close to one share; small-account whole-share compatibility allows a 1-share order this cycle",
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
        "market_status_blend_gate_overlay_capped": "RISK-CAP ({asset})",
        "signal_blend_gate_risk_on": "{trend_symbol} is above the {window}-day gate; hold SOXL {soxl_ratio} + SOXX {soxx_ratio}",
        "signal_blend_gate_defensive": "{trend_symbol} is below the gate; hold SOXX {soxx_ratio}",
        "signal_blend_gate_overlay_capped": "{trend_symbol} remains above the {window}-day gate, but risk cap is active ({reasons}); target {allocation_text}",
        "risk_control_tqqq_volatility_delever_applied": "🛡️ Risk control: QQQ {window}d annualized volatility {volatility} is above {threshold}; {source_symbol} redirects to {redirect_symbol} ({allocation_detail})",
        "risk_control_tqqq_volatility_delever_applied_dynamic": "🛡️ Risk control: QQQ {window}d annualized volatility {volatility} is above effective threshold {threshold} ({threshold_detail}); {source_symbol} redirects to {redirect_symbol} ({allocation_detail})",
        "risk_control_tqqq_volatility_delever_hysteresis": "🛡️ Risk control: QQQ {window}d annualized volatility {volatility} remains above the exit threshold {exit_threshold}; keep {source_symbol} redirected to {redirect_symbol} ({allocation_detail})",
        "risk_control_tqqq_volatility_delever_hysteresis_dynamic": "🛡️ Risk control: QQQ {window}d annualized volatility {volatility} remains above exit threshold {exit_threshold}; entry effective threshold {threshold} ({threshold_detail}); keep {source_symbol} redirected to {redirect_symbol} ({allocation_detail})",
        "tqqq_volatility_delever_allocation_detail": "leveraged sleeve: TQQQ retained {retained_ratio}, {redirect_symbol} {redirected_ratio}",
        "tqqq_signal_reason_entry_trend": "reason: QQQ is above MA200 and MA20 slope is positive",
        "tqqq_signal_reason_entry_pullback": "reason: QQQ is below MA200 but above MA20 with a confirmed pullback rebound",
        "tqqq_signal_reason_hold_trend": "reason: existing risk sleeve remains active while QQQ stays above MA200",
        "tqqq_signal_reason_exit_ma200": "reason: QQQ fell below the MA200 exit line",
        "tqqq_signal_reason_idle_waiting": "reason: waiting for QQQ to reclaim MA200 with positive MA20 slope",
        "tqqq_signal_reason_macro_delever": "reason: macro risk governor reduced leverage",
        "tqqq_signal_reason_macro_defense": "reason: macro risk governor moved the strategy defensive",
        "tqqq_signal_reason_crisis_defense": "reason: crisis defense moved the strategy to the safe sleeve",
        "market_status_risk_on": "Risk on ({asset})",
        "market_status_delever": "Delever ({asset})",
        "signal_risk_on": "SOXL is above the {window}-day average; hold SOXL at risk sleeve {ratio}",
        "signal_delever": "SOXL is below the {window}-day average; switch to SOXX at risk sleeve {ratio}",
        "blend_gate_reason_rsi_cap": "RSI over threshold",
        "blend_gate_reason_bollinger_cap": "price above upper band",
        "blend_gate_reason_volatility_delever": "{symbol} {window}d annualized volatility {volatility} is above {threshold}; redirect SOXL to {redirect_symbol}",
        "blend_gate_reason_volatility_delever_dynamic": "{symbol} {window}d annualized volatility {volatility} is above effective threshold {threshold} ({threshold_detail}); redirect SOXL to {redirect_symbol}",
        "blend_gate_volatility_threshold_detail_dynamic": "dynamic {percentile}, {lookback}d lookback, bounded {floor}-{cap}, samples {sample_count}",
        "blend_gate_volatility_threshold_detail_dynamic_fallback": "dynamic warm-up, fallback fixed {fixed_threshold} (samples {sample_count}/{min_periods}, {percentile})",
        "blend_gate_volatility_threshold_detail_fixed": "fixed threshold {threshold}",
        "small_account_warning_note": "small account warning: portfolio equity {portfolio_equity} is below recommended {min_recommended_equity}; {reason}",
        "small_account_warning_reason_integer_shares_min_position_value_may_prevent_backtest_replication": "integer-share minimum position sizing may prevent backtest replication",
        "strategy_name_tqqq_growth_income": "TQQQ Growth Income",
        "strategy_name_soxl_soxx_trend_income": "SOXL/SOXX Semiconductor Trend Income",
        "strategy_name_global_etf_rotation": "Global ETF Rotation",
        "strategy_name_global_etf_confidence_vol_gate": "Global ETF Confidence Vol Gate",
        "strategy_name_tech_communication_pullback_enhancement": "Tech/Communication Pullback Enhancement",
        "strategy_name_qqq_tech_enhancement": "Tech/Communication Pullback Enhancement",
        "strategy_name_russell_top50_leader_rotation": "Russell Top50 Leader Rotation",
        "strategy_name_nasdaq_sp500_smart_dca": "Nasdaq 100 / S&P 500 Smart DCA",
        "strategy_name_ibit_smart_dca": "IBIT Smart DCA",
        "skip_reason_below_trade_threshold": "below trade threshold",
        "skip_reason_quote_unavailable": "quote unavailable",
        "skip_reason_sell_quantity_zero": "whole-share quantity rounds to 0; no order needed",
        "skip_reason_pending_sell_release": "trim still pending but whole-share sell rounded to 0; buy skipped this cycle to avoid margin",
        "skip_reason_negative_cash": "account cash is negative; buy skipped to avoid additional margin",
        "skip_reason_buy_quantity_zero": "whole-share quantity rounds to 0; no order needed",
        "skip_reason_insufficient_cash_for_whole_share": "insufficient cash for one whole share",
        "skip_reason_broker_rejected": "broker rejected the order",
        "skip_reason_fractional_trading_disclosure_required": "accept Firstrade's Fractional Shares Trading Disclosure first (broker rejection 1219)",
        "skip_reason_unknown": "unknown reason",
        "deferred_orders_line": "ℹ️ [Skipped this cycle] {details}",
        "skip_symbols_reason": "{symbols} ({reason})",
    },
}

if _merge_strategy_plugin_i18n is not None:
    _PLATFORM_I18N = {locale: dict(values) for locale, values in I18N.items()}
    try:
        I18N = _merge_strategy_plugin_i18n(I18N, shared_wins=False)
    except TypeError:
        I18N = _merge_strategy_plugin_i18n(I18N)
        for locale, values in _PLATFORM_I18N.items():
            I18N.setdefault(locale, {}).update(values)


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


def build_strategy_display_name(lang: str, translate_fn):
    """Build a strategy display name resolver consistent with other platforms.

    Returns a callable that resolves a strategy's display name using catalog
    metadata (if available) or the legacy i18n dictionary fallback.
    """
    def strategy_display_name(
        profile: str,
        *,
        fallback_name: str | None = None,
        metadata=None,
    ) -> str:
        if metadata is not None:
            from quant_platform_kit.common.notification_localization import (
                resolve_strategy_display_name,
            )
            return resolve_strategy_display_name(
                lang, metadata, translator=translate_fn,
            )
        key = f"strategy_name_{str(profile or '').strip()}"
        translated = translate_fn(key)
        if translated != key:
            return translated
        fallback = str(fallback_name or "").strip()
        if fallback:
            return fallback
        return str(profile or "").strip()
    return strategy_display_name


def build_sender(token: str | None, chat_id: str | None, *, requests_module=None):
    if requests_module is None:
        import requests as requests_module

    def send_tg_message(message: str) -> None:
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests_module.post(
                url,
                json={"chat_id": chat_id, "text": _break_telegram_market_symbol_auto_links(message)},
                timeout=15,
            )
        except Exception as exc:
            print(f"Telegram send failed: {type(exc).__name__}", flush=True)

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


def _is_dashboard_account_title(line: str, *, translator: Callable[..., str]) -> bool:
    text = str(line or "").strip()
    account_titles = {
        translator("account_overview_title"),
        "📌 Strategy Account",
        "📌 Strategy portfolio",
        "📌 策略账户概览",
    }
    return text in account_titles


def _is_dashboard_holdings_title(line: str, *, translator: Callable[..., str]) -> bool:
    text = str(line or "").strip()
    holdings_titles = {
        translator("holdings_title"),
        "💼 Strategy Holdings",
        "💼 Strategy holdings",
        "💼 策略持仓",
    }
    return text in holdings_titles


def _is_dashboard_account_metric_line(line: str, *, translator: Callable[..., str]) -> bool:
    text = str(line or "").strip()
    lowered = text.lower()
    if not text:
        return False
    if text.startswith((translator("dashboard_label"), "📊 Dashboard", "📊 资产看板")):
        return True
    if text.startswith(("Income:", "收益:", "收入:")):
        return True
    if _DASHBOARD_POSITION_LINE_RE.match(text) and (
        "$" in text or "股" in text or "share" in lowered
    ):
        return True
    metric_labels = {
        translator("total_assets"),
        translator("buying_power"),
        translator("reserved_cash"),
        translator("investable_cash"),
        translator("equity"),
        "Total assets (strategy symbols + cash)",
        "Total assets (strategy symbols + cash, ex-margin)",
        "Total assets (strategy net liquidation)",
        "Buying power",
        "Reserved cash",
        "Investable cash",
        "Equity",
        "总资产（策略标的+现金）",
        "总资产（策略标的+现金，不含融资额度）",
        "总资产（策略净值）",
        "购买力",
        "预留现金",
        "可投资现金",
        "净值",
    }
    return any(label and label.lower() in lowered for label in metric_labels)


def _format_generated_dashboard_lines(
    portfolio: Mapping[str, Any],
    execution: Mapping[str, Any],
    *,
    translator: Callable[..., str],
) -> list[str]:
    lines = [translator("account_overview_title")]
    cash_only_execution = bool(execution.get("cash_only_execution", portfolio.get("cash_only_execution", True)))
    total_equity = _safe_float(portfolio.get("total_equity"))
    if total_equity is not None:
        total_assets_label = "total_assets" if cash_only_execution else "total_assets_margin"
        lines.append(f"  - {translator(total_assets_label)}: {_format_money(total_equity)}")
    buying_power = _safe_float(portfolio.get("liquid_cash"))
    if buying_power is None:
        buying_power = _safe_float(portfolio.get("buying_power"))
    if buying_power is not None:
        buying_power_label = "buying_power" if cash_only_execution else "buying_power_margin"
        lines.append(f"  - {translator(buying_power_label)}: {_format_money(buying_power)}")
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


def _format_dashboard_lines(
    portfolio: Mapping[str, Any],
    execution: Mapping[str, Any],
    *,
    translator: Callable[..., str],
) -> list[str]:
    generated_lines = _format_generated_dashboard_lines(portfolio, execution, translator=translator)
    dashboard_text = str(execution.get("dashboard_text") or "").strip()
    if not dashboard_text:
        return generated_lines

    has_signal_display = bool(str(execution.get("signal_display") or "").strip())
    extra_lines = []
    skipping_dashboard_holdings = False
    for raw_line in dashboard_text.splitlines():
        if not raw_line.strip():
            continue
        localized = _base_localize_notification_text(raw_line.rstrip(), translator=translator)
        if has_signal_display and _is_dashboard_signal_line(localized):
            continue
        if _is_dashboard_holdings_title(localized, translator=translator):
            skipping_dashboard_holdings = True
            continue
        if skipping_dashboard_holdings:
            if raw_line.startswith((" ", "\t", "-")):
                continue
            skipping_dashboard_holdings = False
        if _is_dashboard_account_title(localized, translator=translator):
            continue
        if _is_dashboard_account_metric_line(localized, translator=translator):
            continue
        extra_lines.append(localized)
    return [*generated_lines, *extra_lines]


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


def _format_signal_snapshot_line(snapshot: Any, *, lang: str) -> str:
    if not isinstance(snapshot, Mapping):
        return ""
    market_date = str(snapshot.get("market_date") or snapshot.get("signal_as_of") or "").strip()
    source = str(snapshot.get("latest_price_source") or "").strip()
    warning = snapshot.get("data_freshness_warning")
    if not market_date and not source and warning in (None, "", False):
        return ""
    use_zh = str(lang or "").lower().startswith("zh")
    if use_zh:
        parts = [
            f"日期 {market_date or '未知'}",
            f"数据源 {_localize_price_source_label(source, locale=lang)}",
        ]
        if warning not in (None, "", False):
            parts.append(f"提示 {_base_localize_notification_text(warning, translator=build_translator(lang))}")
        return "🧾 信号快照: " + " | ".join(parts)
    parts = [
        f"date {market_date or 'unknown'}",
        f"source {_localize_price_source_label(source, locale=lang)}",
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


def _format_tqqq_risk_control_lines(
    execution: Mapping[str, Any],
    *,
    translator: Callable[..., str],
) -> list[str]:
    return _build_tqqq_risk_control_lines_shared(execution, translator=translator)


def _format_signal_lines(execution: Mapping[str, Any], *, translator: Callable[..., str]) -> list[str]:
    status = _first_summary(execution.get("status_display"), translator=translator)
    signal = _first_summary(execution.get("signal_display"), translator=translator)
    lines = []
    if status and status != signal:
        lines.append(translator("market_status_line", status=status))
    lines.extend(_format_tqqq_risk_control_lines(execution, translator=translator))
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
        notional_usd = _safe_float(order.get("notional_usd"))
        quantity = (
            _format_money(notional_usd)
            if notional_usd is not None and notional_usd > 0.0
            else _format_shares(order.get("quantity"), translator=translator)
        )
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
                "submitted_market_order" if order_type == "market" else "submitted_limit_order",
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
        if symbols:
            parts.append(
                translator(
                    "skip_symbols_reason",
                    symbols=",".join(symbols),
                    reason=reason,
                )
            )
        else:
            parts.append(reason)
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
    strategy_metadata = result.get("strategy_metadata")
    if strategy_metadata is not None:
        from quant_platform_kit.common.notification_localization import (
            resolve_strategy_display_name,
        )
        strategy_name = resolve_strategy_display_name(
            lang, strategy_metadata, translator=translator,
        )
    else:
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
        elif bool(result.get("execution_block_retryable")):
            banner_key = "execution_blocked_retryable_banner"
        else:
            banner_key = "execution_blocked_banner"
        lines.append(translator(banner_key, reason=reason))

    dashboard_lines = _format_dashboard_lines(portfolio, execution, translator=translator)
    if dashboard_lines:
        lines.append(SEPARATOR)
        lines.extend(dashboard_lines)
    lines.append(SEPARATOR)
    lines.extend(target_diff_lines)
    lines.extend(
        str(line).strip()
        for line in result.get("strategy_plugin_error_lines") or ()
        if str(line).strip()
    )
    execution_notes = tuple(result.get("execution_notes") or allocation.get("small_account_whole_share_cash_notes") or ())
    lines.extend(format_small_account_cash_substitution_notes(execution_notes, translator=translator))
    lines.extend(format_small_account_allocation_drift_notes(execution_notes, translator=translator))
    lines.extend(
        format_small_account_whole_share_bootstrap_notes(
            allocation.get("small_account_whole_share_bootstrap_symbols") or (),
            translator=translator,
        )
    )
    if submitted:
        lines.append(translator("order_logs_title"))
        lines.extend(_format_order_lines(submitted, dry_run_only=dry_run_only, translator=translator))
        meaningful_skipped = [
            item
            for item in skipped
            if str(item.get("reason") or "") != "below_trade_threshold"
        ]
        if meaningful_skipped:
            lines.append(
                translator(
                    "deferred_orders_line",
                    details=_format_skipped_reason(meaningful_skipped, translator=translator),
                )
            )
    elif skipped and has_rebalance_attempt:
        lines.append(translator("order_logs_title"))
        reason = _format_skipped_reason(skipped, translator=translator)
        lines.append(translator("no_order_submitted", reason=reason))
    else:
        lines.append(translator("no_rebalance_needed"))
    return "\n".join(str(line) for line in lines if str(line).strip())
