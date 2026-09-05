"""Cloud Run entrypoint for Firstrade platform validation and dry-run cycles."""

from __future__ import annotations

import os
import re
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request

from quant_platform_kit.common.health import register_health_endpoint
from quant_platform_kit.common.execution_commands import build_execution_command_store_from_env
from quant_platform_kit.common.platform_runner import dispatch_due_monitors, load_monitor_targets
from quant_platform_kit.common.strategy_release import build_runtime_loaded_receipt
from application.firstrade_client import (
    FirstradeBrokerClient,
    FirstradeCredentials,
    FirstradePlatformError,
    is_live_trading_enabled,
    mask_account_id,
)
from application.paper_execution_admission import (
    evaluate_paper_dry_run_admission,
    paper_dry_run_admission_requested,
)
from application.paper_execution_command_consumer import (
    consume_due_paper_execution_commands,
    resolve_paper_execution_command_consumer_enabled,
)
from application.rebalance_service import run_strategy_cycle
from application.execution_receipt_adapter import (
    attach_strategy_result_execution_receipt,
    attach_unknown_failure_execution_receipt,
)
from application.runtime_broker_adapters import build_runtime_broker_adapters
from application.broker_reconciliation import (
    FirstradeReconciliationUnavailable,
    build_reconciliation_candidate,
    collect_read_only_reconciliation_observations,
    reconciliation_enabled,
    validate_reconciliation_candidate,
    validate_reconciliation_preconditions,
)
from application.session_check_service import run_session_check
from notifications.telegram import build_sender
from quant_platform_kit.common.runtime_reports import (
    append_runtime_report_error,
    build_runtime_report_base,
    finalize_runtime_report,
    persist_runtime_report,
)
from entrypoints.cloud_run import is_market_open_now
from runtime_config_support import (
    PlatformRuntimeSettings,
    _runtime_target_enabled_env,
    load_platform_runtime_settings,
)
from strategy_registry import get_platform_profile_status_matrix
from strategy_runtime import load_strategy_runtime

MARKET_CALENDAR = os.getenv("FIRSTRADE_MARKET_CALENDAR", "NYSE")
MARKET_TIMEZONE = os.getenv("FIRSTRADE_MARKET_TIMEZONE", "America/New_York")

app = Flask(__name__)
register_health_endpoint(app)  # GET /health /healthz

def _build_read_only_reconciliation_client():
    return FirstradeBrokerClient(
        FirstradeCredentials.from_env(include_login_credentials=False),
        live_trading_enabled=False,
    ).connect_read_only()


READ_ONLY_BROKER_RECONCILIATION_CLIENT_BUILDER = _build_read_only_reconciliation_client

_REDACTED = "<redacted>"
_TELEGRAM_BOT_PATH_RE = re.compile(r"(?i)(/bot)([^/\s]+)")
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&](?:access[_-]?token|api[_-]?key|auth[_-]?token|key|password|secret|signature|token)=)([^&\s]+)"
)
_AUTH_HEADER_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+([A-Za-z0-9._~+/=-]{8,})")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|auth[_-]?token|credential|password|private[_-]?key|secret|token)\s*[:=]\s*([\"']?)([^\"'\s,;]{8,})([\"']?)"
)


def get_project_id() -> str | None:
    return os.getenv("GOOGLE_CLOUD_PROJECT")


def _flag(name: str, default: str = "false") -> bool:
    return (os.getenv(name, default) or "").strip().lower() == "true"


def _split_env_list(value: str | None) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in str(value or "").replace(";", ",").split(",")
        if item.strip()
    )


def redact_sensitive_text(value: object) -> str:
    text = str(value)
    text = _TELEGRAM_BOT_PATH_RE.sub(r"\1" + _REDACTED, text)
    text = _SENSITIVE_QUERY_RE.sub(r"\1" + _REDACTED, text)
    text = _AUTH_HEADER_RE.sub(r"\1 " + _REDACTED, text)
    return _ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}={_REDACTED}", text)


def _get_telegram_token() -> str:
    try:
        from quant_platform_kit.cloud import get_secret_store

        return get_secret_store().get_secret("firstrade-telegram-token", project_id="firstradequant")
    except Exception:
        return os.environ.get("TELEGRAM_TOKEN", "")


def _telegram_notification_targets() -> tuple[tuple[str, str], ...]:
    targets: list[tuple[str, str]] = []
    main_token = _get_telegram_token()
    main_chat_id = os.getenv("QSL_GLOBAL_TELEGRAM_CHAT_ID") or os.getenv("GLOBAL_TELEGRAM_CHAT_ID")
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
    error_text = _safe_exception_text(exc, include_type=True)
    if len(error_text) > 1200:
        error_text = error_text[:1197] + "..."
    is_health_check = request.path == "/probe"
    if str(os.getenv("QSL_NOTIFY_LANG") or os.getenv("NOTIFY_LANG") or "").strip().lower().startswith("zh"):
        return "\n".join(
            (
                "Firstrade 健康检查失败" if is_health_check else "Firstrade 策略运行失败",
                f"服务: {os.getenv('K_SERVICE') or 'firstrade-quant-service'}",
                f"版本: {os.getenv('K_REVISION') or '<unknown>'}",
                f"路由: {request.method} {request.path}",
                f"策略: {os.getenv('STRATEGY_PROFILE') or '<unset>'}",
                f"账户范围: {os.getenv('ACCOUNT_REGION') or '<unset>'}",
                f"错误: {error_text}",
            )
        )
    return "\n".join(
        (
            "Firstrade health check failed" if is_health_check else "Firstrade strategy run failed",
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
            print(
                f"Firstrade runtime error Telegram send failed: {redact_sensitive_text(send_exc)}",
                flush=True,
            )
    return attempted


def _safe_exception_text(exc: Exception, *, include_type: bool = False) -> str:
    text = redact_sensitive_text(exc)
    return f"{type(exc).__name__}: {text}" if include_type else text


def _handle_strategy_run_exception(exc: Exception) -> bool:
    print(f"Firstrade strategy run failed: {_safe_exception_text(exc, include_type=True)}", flush=True)
    for line in traceback.format_exception(type(exc), exc, exc.__traceback__):
        print(redact_sensitive_text(line.rstrip()), flush=True)
    return _notify_runtime_error(exc)


def _build_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _service_name() -> str:
    return os.getenv("K_SERVICE") or "firstrade-quant-service"


def _runtime_settings(*, dry_run_override: bool | None = None) -> PlatformRuntimeSettings:
    settings = load_platform_runtime_settings(project_id_resolver=get_project_id)
    if dry_run_override is None:
        return settings
    runtime_target = settings.runtime_target
    if runtime_target is not None:
        runtime_target = replace(runtime_target, dry_run_only=bool(dry_run_override))
    return replace(
        settings,
        dry_run_only=bool(dry_run_override),
        live_trading_enabled=False if dry_run_override else settings.live_trading_enabled,
        runtime_target=runtime_target,
    )


def _build_runtime_report(settings: PlatformRuntimeSettings, *, dry_run: bool) -> dict[str, Any]:
    runtime_target = settings.runtime_target
    return build_runtime_report_base(
        platform="firstrade",
        deploy_target="cloud_run",
        service_name=_service_name(),
        strategy_profile=settings.strategy_profile,
        run_id=_build_run_id(),
        run_source="cloud_run",
        runtime_target=runtime_target,
        strategy_domain=settings.strategy_domain,
        account_scope=(
            getattr(runtime_target, "account_scope", None)
            if runtime_target is not None
            else settings.account_region
        ),
        account_region=settings.account_region,
        project_id=settings.project_id or get_project_id(),
        dry_run=dry_run,
        started_at=datetime.now(timezone.utc),
        summary={
            "strategy_display_name": settings.strategy_display_name,
            "strategy_display_name_localized": settings.strategy_display_name,
        },
    )


def _plugin_report_fields(result: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in result.items():
        if key == "strategy_plugins" or key.startswith("strategy_plugin_alert_"):
            fields[key] = value
    return fields


def _strategy_result_summary(result: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    submitted_orders = list(result.get("submitted_orders") or ())
    skipped_orders = list(result.get("skipped_orders") or ())
    execution_notes = list(result.get("execution_notes") or ())
    orders_previewed_count = len(submitted_orders) if dry_run else 0
    summary = {
        "action_done": bool(result.get("action_done")),
        "execution_status": result.get("strategy_run_stage"),
        "order_events_count": len(submitted_orders),
        "orders_previewed_count": orders_previewed_count,
        "orders_skipped_count": len(skipped_orders),
        "notes_count": len(execution_notes),
        "dry_run_order_preview_available": bool(dry_run and orders_previewed_count > 0),
        "strategy_run_period": result.get("strategy_run_period"),
        "strategy_run_stage": result.get("strategy_run_stage"),
        "strategy_run_persisted": result.get("strategy_run_persisted"),
        "session_reused": bool(result.get("session_reused")),
        "live_trading_enabled": bool(result.get("live_trading_enabled")),
        "notification_sent": bool(result.get("notification_sent")),
        "notification_suppressed": bool(result.get("notification_suppressed")),
        **_plugin_report_fields(result),
    }
    if result.get("notification_error"):
        summary["notification_error"] = result.get("notification_error")
    if dry_run and submitted_orders:
        summary["orders_previewed"] = submitted_orders
    if skipped_orders:
        summary["orders_skipped"] = skipped_orders
    if execution_notes:
        summary["execution_notes"] = execution_notes
    return summary


def _strategy_result_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    signal_snapshot = result.get("signal_snapshot")
    if signal_snapshot:
        diagnostics["signal_snapshot"] = signal_snapshot
    for key in (
        "strategy_plugin_error",
        "strategy_plugin_alert_error",
        "strategy_run_persistence_error",
        "execution_blocked",
        "execution_block_retryable",
        "execution_blocking_skips",
        "funding_blocked",
        "error",
    ):
        value = result.get(key)
        if value not in (None, "", [], {}):
            diagnostics[key] = value
    return diagnostics


def _strategy_result_http_status(result: dict[str, Any]) -> int:
    if result.get("execution_blocked") and result.get("execution_block_retryable"):
        return 500
    return 200


def _persist_runtime_report(report: dict[str, Any]) -> str | None:
    persisted = persist_runtime_report(
        report,
        cloud_prefix_uri=os.getenv("QSL_EXECUTION_REPORT_GCS_URI") or os.getenv("EXECUTION_REPORT_GCS_URI"),
        project_id=get_project_id(),
    )
    if isinstance(persisted, str):
        return persisted
    return getattr(persisted, "gcs_uri", None) or getattr(persisted, "local_path", None)


def _force_strategy_run_env() -> bool:
    return _flag("FIRSTRADE_FORCE_RUN")


def _market_open_skip_payload(*, market_open: bool, error: Exception | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "status": "skipped",
        "skip_reason": "market_closed",
        "action_done": False,
        "submitted_orders": [],
        "skipped_orders": [{"reason": "market_closed"}],
    }
    if error is not None:
        payload["market_hours_check_error"] = f"{type(error).__name__}: {error}"
    if _force_strategy_run_env() and not market_open:
        payload["market_hours_bypass_requested"] = True
    return payload


def _should_skip_for_market_hours() -> tuple[bool, dict[str, Any] | None]:
    if _force_strategy_run_env():
        return False, None
    market_open = is_market_open_now(
        calendar_name=MARKET_CALENDAR,
        timezone_name=MARKET_TIMEZONE,
    )
    error = None
    if isinstance(market_open, tuple):
        market_open, error = market_open
    if market_open:
        return False, None
    print(
        "Firstrade strategy run skipped: outside US equity regular session.",
        flush=True,
    )
    if error is not None:
        print(f"Firstrade market hours check failed: {error}", flush=True)
    return True, _market_open_skip_payload(market_open=False, error=error)


def _run_strategy_cycle_with_report(
    *,
    dry_run_override: bool | None = None,
    send_cycle_notification: bool = True,
    dispatch_plugin_alerts: bool = True,
) -> dict[str, Any]:
    settings = _runtime_settings(dry_run_override=dry_run_override)
    dry_run = bool(settings.dry_run_only)
    report = _build_runtime_report(settings, dry_run=dry_run)
    try:
        result = run_strategy_cycle(
            runtime_settings=settings,
            send_cycle_notification=send_cycle_notification,
            dispatch_plugin_alerts=dispatch_plugin_alerts,
        )
        attach_strategy_result_execution_receipt(report, result, dry_run=dry_run)
        finalize_runtime_report(
            report,
            status="ok" if result.get("ok", True) else "error",
            summary=_strategy_result_summary(result, dry_run=dry_run),
            diagnostics=_strategy_result_diagnostics(result),
        )
        try:
            report_path = _persist_runtime_report(report)
            if report_path:
                print(f"execution_report {report_path}", flush=True)
        except Exception as persist_exc:
            print(f"failed to persist execution report: {persist_exc}", flush=True)
        return result
    except Exception as exc:
        append_runtime_report_error(
            report,
            stage="strategy_cycle",
            message=str(exc),
            error_type=type(exc).__name__,
        )
        attach_unknown_failure_execution_receipt(report)
        finalize_runtime_report(report, status="error")
        try:
            report_path = _persist_runtime_report(report)
            if report_path:
                print(f"execution_report {report_path}", flush=True)
        except Exception as persist_exc:
            print(f"failed to persist execution report: {persist_exc}", flush=True)
        raise


def _evaluate_paper_dry_run_admission() -> dict[str, object] | None:
    """Load only release identity needed for an opt-in pre-preview gate."""
    if not paper_dry_run_admission_requested(os.environ):
        return None
    try:
        runtime_target = _runtime_settings(dry_run_override=True).runtime_target
    except (EnvironmentError, ValueError):
        runtime_target = None
    return evaluate_paper_dry_run_admission(runtime_target=runtime_target, env=os.environ)


def _paper_command_consumer_session_date() -> str:
    return datetime.now(ZoneInfo(MARKET_TIMEZONE)).date().isoformat()


def _read_only_execution_ledger_digest(*, runtime_target: object, project_id: str | None) -> tuple[str, int]:
    """Read the durable execution ledger without changing it."""

    from quant_platform_kit.common.execution_state import build_execution_marker_store_from_env

    store = build_execution_marker_store_from_env(
        platform_env_prefix="FIRSTRADE",
        env_reader=os.getenv,
        project_id=project_id,
    )
    return store.calculate_recent_ledger_digest(
        platform=str(getattr(runtime_target, "platform_id", "") or ""),
        strategy_profile=str(getattr(runtime_target, "strategy_profile", "") or ""),
        account_scope=str(getattr(runtime_target, "account_scope", "") or ""),
        execution_mode="live",
    )


def _handle_reconciliation():
    """Return a private, redacted, no-order reconciliation candidate only."""

    if not reconciliation_enabled(os.getenv):
        return jsonify({"status": "blocked", "reason": "broker_reconciliation_disabled"}), 503
    client = None
    try:
        settings = _runtime_settings()
        runtime_target = settings.runtime_target
        validate_reconciliation_preconditions(
            runtime_target=runtime_target,
            client_builder=READ_ONLY_BROKER_RECONCILIATION_CLIENT_BUILDER,
            env_reader=os.getenv,
        )
        requested_account = str(os.getenv("FIRSTRADE_ACCOUNT") or "").strip()
        if not requested_account:
            raise FirstradeReconciliationUnavailable("Firstrade reconciliation requires an explicit account.")
        client = READ_ONLY_BROKER_RECONCILIATION_CLIENT_BUILDER()
        observations = collect_read_only_reconciliation_observations(
            client,
            requested_account=requested_account,
        )
        candidate = build_reconciliation_candidate(
            observations=observations,
            runtime_target=runtime_target,
            project_id=settings.project_id or get_project_id(),
            ledger_digest_reader=lambda: _read_only_execution_ledger_digest(
                runtime_target=runtime_target,
                project_id=settings.project_id or get_project_id(),
            ),
            env_reader=os.getenv,
        )
        return jsonify(validate_reconciliation_candidate(candidate)), 200
    except FirstradeReconciliationUnavailable:
        return jsonify({"status": "blocked", "reason": "broker_reconciliation_unavailable"}), 503
    except Exception:
        return jsonify({"status": "blocked", "reason": "broker_reconciliation_unavailable"}), 503
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass  # Cleanup must not expose provider details or change the safe response.


def _paper_command_consumer_runtime_is_isolated(settings: PlatformRuntimeSettings) -> bool:
    """Require an explicitly disabled, cash-only paper runtime."""

    runtime_target = settings.runtime_target
    return bool(
        runtime_target is not None
        and settings.dry_run_only
        and not settings.runtime_target_enabled
        and settings.cash_only_execution
        and str(getattr(runtime_target, "execution_mode", "") or "").strip().lower() == "paper"
    )


def run_paper_execution_command_consumer() -> dict[str, object]:
    """Verify due paper commands through read-only Firstrade account evidence.

    This function is intentionally outside ``run_strategy_cycle``. It creates
    neither an execution port nor a stock-order request, and opens the
    Firstrade session only after the shared consumer accepts the release and
    exact platform/account/strategy delivery binding.
    """

    settings = _runtime_settings()
    if not _paper_command_consumer_runtime_is_isolated(settings):
        raise RuntimeError(
            "paper command consumer requires RUNTIME_TARGET_ENABLED=false, "
            "FIRSTRADE_DRY_RUN_ONLY=true, a paper runtime target, and cash-only execution"
        )
    if not resolve_paper_execution_command_consumer_enabled(
        env_reader=os.getenv,
        dry_run_only=settings.dry_run_only,
    ):
        raise RuntimeError("paper command consumer is not enabled")
    requested_account = str(os.getenv("FIRSTRADE_ACCOUNT") or "").strip()
    if not requested_account:
        raise RuntimeError("paper command consumer requires an explicit FIRSTRADE_ACCOUNT")

    runtime_target = settings.runtime_target
    expected_release = getattr(runtime_target, "strategy_release", None)
    expected_binding = {
        "platform": "firstrade",
        "account_scope": str(getattr(runtime_target, "account_scope", "") or "unknown"),
        "strategy_profile": str(getattr(runtime_target, "strategy_profile", "") or "unknown"),
    }
    store = build_execution_command_store_from_env(
        platform_env_prefix="FIRSTRADE",
        env_reader=os.getenv,
        project_id=settings.project_id or get_project_id(),
    )
    if not store.cloud_prefix_uri and not store.local_dir:
        raise RuntimeError("Firstrade paper command consumer requires an execution command store")
    strategy_runtime = load_strategy_runtime(
        settings.strategy_profile,
        runtime_settings=settings,
        logger=lambda message: print(message, flush=True),
    )
    managed_symbols = tuple(strategy_runtime.managed_symbols)
    if not managed_symbols:
        raise RuntimeError("Firstrade paper command consumer requires configured managed symbols")

    report = _build_runtime_report(settings, dry_run=True)
    broker_adapters = None
    market_data_port = None

    def _broker_adapters():
        nonlocal broker_adapters
        if broker_adapters is None:
            credentials = FirstradeCredentials.from_env()
            client = FirstradeBrokerClient(
                credentials,
                live_trading_enabled=False,
            ).connect()
            account = client.select_account(requested_account)
            broker_adapters = build_runtime_broker_adapters(
                client=client,
                account=account,
                strategy_symbols=managed_symbols,
                account_hash=mask_account_id(account),
                live_orders=False,
                live_order_ack=False,
                cash_only_execution=True,
            )
        return broker_adapters

    def _load_portfolio():
        return _broker_adapters().build_reconciled_paper_portfolio_snapshot()

    def _load_quote(symbol: str):
        nonlocal market_data_port
        if market_data_port is None:
            market_data_port = _broker_adapters().build_market_data_port()
        return market_data_port.get_quote(symbol)

    try:
        result = consume_due_paper_execution_commands(
            store=store,
            as_of_session=_paper_command_consumer_session_date(),
            claimant=_service_name(),
            portfolio_loader=_load_portfolio,
            quote_loader=_load_quote,
            managed_symbols=managed_symbols,
            runtime_release_receipt=build_runtime_loaded_receipt(
                strategy_release=expected_release,
            ),
            expected_strategy_release=expected_release,
            expected_command_binding=expected_binding,
        )
        finalize_runtime_report(
            report,
            status="ok" if result.get("status") == "ok" else "skipped",
            summary={"paper_execution_command_consumer": result},
        )
        return result
    except Exception as exc:
        append_runtime_report_error(
            report,
            stage="paper_execution_command_consumer",
            message=_safe_exception_text(exc),
            error_type=type(exc).__name__,
        )
        finalize_runtime_report(report, status="error")
        raise
    finally:
        try:
            report_path = _persist_runtime_report(report)
            if report_path:
                print(f"execution_report {report_path}", flush=True)
        except Exception as persist_exc:
            print(f"failed to persist execution report: {persist_exc}", flush=True)


@app.get("/")
def service_info():
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
        return jsonify({"ok": False, "error": _safe_exception_text(exc)}), 500


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
        notification_attempted = _notify_runtime_error(exc)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": _safe_exception_text(exc),
                    "runtime_error_notification_attempted": notification_attempted,
                }
            ),
            500,
        )
    except Exception as exc:
        notification_attempted = _notify_runtime_error(exc)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": _safe_exception_text(exc, include_type=True),
                    "runtime_error_notification_attempted": notification_attempted,
                }
            ),
            500,
        )


@app.post("/run")
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
    if not _runtime_target_enabled_env():
        return jsonify({"ok": True, "status": "skipped", "skip_reason": "runtime_target_disabled"}), 200
    skip_for_market, skip_payload = _should_skip_for_market_hours()
    if skip_for_market and skip_payload is not None:
        return jsonify(skip_payload), 200
    try:
        result = _run_strategy_cycle_with_report()
        return jsonify(result), _strategy_result_http_status(result)
    except (FirstradePlatformError, EnvironmentError, ValueError) as exc:
        notification_attempted = _handle_strategy_run_exception(exc)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": _safe_exception_text(exc),
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
                    "error": _safe_exception_text(exc, include_type=True),
                    "runtime_error_notification_attempted": notification_attempted,
                }
            ),
            500,
        )


@app.post("/dry-run")
@app.get("/dry-run")
def dry_run():
    skip_for_market, skip_payload = _should_skip_for_market_hours()
    if skip_for_market and skip_payload is not None:
        return jsonify(skip_payload), 200
    try:
        admission_audit = _evaluate_paper_dry_run_admission()
        if admission_audit is not None and admission_audit["status"] != "admitted":
            return (
                jsonify(
                    {
                        "ok": False,
                        "status": "blocked",
                        "action_done": False,
                        "submitted_orders": [],
                        "skipped_orders": [{"reason": "paper_execution_admission_blocked"}],
                        "paper_execution_admission": admission_audit,
                    }
                ),
                409,
            )
        result = _run_strategy_cycle_with_report(
            dry_run_override=True,
            send_cycle_notification=False,
            dispatch_plugin_alerts=False,
        )
        if admission_audit is not None:
            result = {**result, "paper_execution_admission": admission_audit}
        return jsonify(result)
    except (FirstradePlatformError, EnvironmentError, ValueError) as exc:
        notification_attempted = _handle_strategy_run_exception(exc)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": _safe_exception_text(exc),
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
                    "error": _safe_exception_text(exc, include_type=True),
                    "runtime_error_notification_attempted": notification_attempted,
                }
            ),
            500,
        )


@app.post("/paper-command-consumer")
def paper_execution_command_consumer():
    """Manual-only endpoint for isolated paper command reconciliation."""

    try:
        return jsonify(run_paper_execution_command_consumer())
    except (FirstradePlatformError, EnvironmentError, ValueError) as exc:
        notification_attempted = _handle_strategy_run_exception(exc)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": _safe_exception_text(exc),
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
                    "error": _safe_exception_text(exc, include_type=True),
                    "runtime_error_notification_attempted": notification_attempted,
                }
            ),
            500,
        )


@app.post("/reconcile")
def reconcile():
    return _handle_reconciliation()


@app.post("/probe")
def probe():
    return session_check()


@app.post("/monitor-dispatch")
@app.get("/monitor-dispatch")
def monitor_dispatch():
    if request.method == "GET":
        return jsonify({"ok": True, "message": "use POST to dispatch due monitor checks"})
    try:
        return jsonify(dispatch_due_monitors(load_monitor_targets()))
    except Exception as exc:
        notification_attempted = _handle_strategy_run_exception(exc)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": _safe_exception_text(exc, include_type=True),
                    "runtime_error_notification_attempted": notification_attempted,
                }
            ),
            500,
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
