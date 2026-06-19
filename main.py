"""Cloud Run entrypoint for Firstrade platform validation and dry-run cycles."""

from __future__ import annotations

import os
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from flask import Flask, jsonify, request

from application.monitor_dispatcher import dispatch_due_monitors, load_monitor_targets
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
from quant_platform_kit.common.runtime_reports import (
    append_runtime_report_error,
    build_runtime_report_base,
    finalize_runtime_report,
    persist_runtime_report,
)
from runtime_config_support import (
    PlatformRuntimeSettings,
    _runtime_target_enabled_env,
    load_platform_runtime_settings,
)
from strategy_registry import get_platform_profile_status_matrix

app = Flask(__name__)


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
    is_health_check = request.path == "/probe"
    if str(os.getenv("NOTIFY_LANG") or "").strip().lower().startswith("zh"):
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
            print(f"Firstrade runtime error Telegram send failed: {send_exc}", flush=True)
    return attempted


def _handle_strategy_run_exception(exc: Exception) -> bool:
    print(f"Firstrade strategy run failed: {type(exc).__name__}: {exc}", flush=True)
    traceback.print_exc()
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
    if result.get("execution_blocked") and result.get("execution_block_retryable") and not result.get("funding_blocked"):
        return 500
    return 200


def _persist_runtime_report(report: dict[str, Any]) -> str | None:
    persisted = persist_runtime_report(
        report,
        gcs_prefix_uri=os.getenv("EXECUTION_REPORT_GCS_URI"),
        gcp_project_id=get_project_id(),
    )
    if isinstance(persisted, str):
        return persisted
    return getattr(persisted, "gcs_uri", None) or getattr(persisted, "local_path", None)


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
        finalize_runtime_report(report, status="error")
        try:
            report_path = _persist_runtime_report(report)
            if report_path:
                print(f"execution_report {report_path}", flush=True)
        except Exception as persist_exc:
            print(f"failed to persist execution report: {persist_exc}", flush=True)
        raise


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
                    "error": str(exc),
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
                    "error": f"{type(exc).__name__}: {exc}",
                    "runtime_error_notification_attempted": notification_attempted,
                }
            ),
            500,
        )


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
    if not _runtime_target_enabled_env():
        return jsonify({"ok": True, "status": "skipped", "skip_reason": "runtime_target_disabled"}), 200
    try:
        result = _run_strategy_cycle_with_report()
        return jsonify(result), _strategy_result_http_status(result)
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


@app.post("/dry-run")
@app.get("/dry-run")
def dry_run():
    try:
        return jsonify(
            _run_strategy_cycle_with_report(
                dry_run_override=True,
                send_cycle_notification=False,
                dispatch_plugin_alerts=False,
            )
        )
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


@app.post("/probe")
@app.get("/probe")
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
                    "error": f"{type(exc).__name__}: {exc}",
                    "runtime_error_notification_attempted": notification_attempted,
                }
            ),
            500,
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
