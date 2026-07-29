#!/usr/bin/env python3
"""Reconcile Cloud Run runtime state after deploy/env sync.

This script keeps the runtime logic minimal and explicit:
- reconcile Cloud Run traffic to the latest ready revision and verify commit-sha
- delete only explicit legacy Cloud Scheduler jobs
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping


RunGcloud = Callable[[list[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class RuntimeContext:
    project_id: str
    service_name: str
    region: str
    scheduler_location: str

def _parse_sync_plan(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if not raw:
        return {}
    payload = json.loads(raw)
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"targets": payload}
    raise ValueError("SYNC_PLAN_JSON must be a JSON object or array")


def _first_target(plan: Mapping[str, Any]) -> Mapping[str, Any]:
    targets = plan.get("targets") or []
    if not isinstance(targets, list) or not targets:
        return {}
    first = targets[0]
    return first if isinstance(first, Mapping) else {}


def _resolve_context(env: Mapping[str, str] = os.environ) -> tuple[RuntimeContext, dict[str, Any]]:
    plan = _parse_sync_plan(str(env.get("SYNC_PLAN_JSON", "") or ""))
    target = _first_target(plan)
    configured_service = str(env.get("CLOUD_RUN_SERVICE", "") or "").strip()
    targets = plan.get("targets")
    if configured_service and isinstance(targets, list) and targets:
        matching_targets = [
            candidate
            for candidate in targets
            if isinstance(candidate, Mapping)
            and configured_service
            in {
                str(candidate.get("service_name") or "").strip(),
                str(candidate.get("service") or "").strip(),
                str(candidate.get("cloud_run_service") or "").strip(),
            }
        ]
        if len(matching_targets) != 1:
            raise ValueError(
                f"CLOUD_RUN_SERVICE {configured_service} does not match any sync-plan target"
            )
        target = matching_targets[0]

    service_name = (
        configured_service
        or str(target.get("service_name") or "").strip()
        or str(target.get("service") or "").strip()
        or str(target.get("cloud_run_service") or "").strip()
    )
    region = (
        str(env.get("CLOUD_RUN_REGION", "") or "").strip()
        or str(target.get("region") or "").strip()
    )
    project_id = str(env.get("GCP_PROJECT_ID", "") or "").strip()
    scheduler_location = (
        str(env.get("CLOUD_SCHEDULER_LOCATION", "") or "").strip()
        or region
    )

    missing = [
        name
        for name, value in (
            ("GCP_PROJECT_ID", project_id),
            ("CLOUD_RUN_SERVICE", service_name),
            ("CLOUD_RUN_REGION", region),
            ("CLOUD_SCHEDULER_LOCATION", scheduler_location),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required runtime context: {', '.join(missing)}")

    return (
        RuntimeContext(
            project_id=project_id,
            service_name=service_name,
            region=region,
            scheduler_location=scheduler_location,
        ),
        plan,
    )


def _run_gcloud(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gcloud", *command],
        check=False,
        text=True,
        capture_output=True,
    )


def _run_gcloud_json(command: list[str], run_gcloud: RunGcloud = _run_gcloud) -> dict[str, Any]:
    result = run_gcloud(command)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(
            f"gcloud {' '.join(command)} failed with exit code {result.returncode}"
            + (f": {stderr}" if stderr else "")
        )
    raw = (result.stdout or "").strip()
    return json.loads(raw) if raw else {}


def _run_gcloud_ok(command: list[str], run_gcloud: RunGcloud = _run_gcloud) -> None:
    result = run_gcloud(command)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(
            f"gcloud {' '.join(command)} failed with exit code {result.returncode}"
            + (f": {stderr}" if stderr else "")
        )


def _describe_service(ctx: RuntimeContext, run_gcloud: RunGcloud = _run_gcloud) -> dict[str, Any]:
    return _run_gcloud_json(
        [
            "run",
            "services",
            "describe",
            ctx.service_name,
            "--project",
            ctx.project_id,
            "--region",
            ctx.region,
            "--format=json",
        ],
        run_gcloud=run_gcloud,
    )


def _describe_revision(
    ctx: RuntimeContext,
    revision_name: str,
    run_gcloud: RunGcloud = _run_gcloud,
) -> dict[str, Any]:
    return _run_gcloud_json(
        [
            "run",
            "revisions",
            "describe",
            revision_name,
            "--project",
            ctx.project_id,
            "--region",
            ctx.region,
            "--format=json",
        ],
        run_gcloud=run_gcloud,
    )


def _wait_for_latest_ready_revision(
    ctx: RuntimeContext,
    *,
    run_gcloud: RunGcloud = _run_gcloud,
    timeout_seconds: int = 1800,
    poll_seconds: int = 10,
) -> tuple[dict[str, Any], str]:
    deadline = time.monotonic() + timeout_seconds
    last_revision = ""
    while True:
        service = _describe_service(ctx, run_gcloud=run_gcloud)
        latest_revision = str(
            service.get("status", {}).get("latestReadyRevisionName") or ""
        ).strip()
        if latest_revision:
            return service, latest_revision
        last_revision = latest_revision
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Timed out waiting for latest ready revision on {ctx.service_name}"
                + (f"; last seen: {last_revision or '<none>'}" if last_revision else "")
            )
        time.sleep(poll_seconds)


def _traffic_is_reconciled(service: Mapping[str, Any], latest_revision: str) -> bool:
    traffic = service.get("status", {}).get("traffic", []) or []
    positive = [
        entry
        for entry in traffic
        if isinstance(entry, Mapping) and int(entry.get("percent", 0) or 0) > 0
    ]
    if len(positive) != 1:
        return False
    entry = positive[0]
    return int(entry.get("percent", 0) or 0) == 100 and (
        entry.get("latestRevision") is True or str(entry.get("revisionName") or "") == latest_revision
    )


def reconcile_traffic(
    env: Mapping[str, str] = os.environ,
    *,
    run_gcloud: RunGcloud = _run_gcloud,
) -> None:
    ctx, _plan = _resolve_context(env)
    target_sha = str(env.get("GITHUB_SHA", "") or "").strip()
    if not target_sha:
        raise ValueError("GITHUB_SHA is required for traffic reconciliation")

    service, latest_revision = _wait_for_latest_ready_revision(ctx, run_gcloud=run_gcloud)
    revision = _describe_revision(ctx, latest_revision, run_gcloud=run_gcloud)
    revision_sha = str(
        revision.get("metadata", {}).get("labels", {}).get("commit-sha") or ""
    ).strip()
    if revision_sha != target_sha:
        raise RuntimeError(
            f"Latest ready revision {latest_revision} on {ctx.service_name} has commit-sha "
            f"{revision_sha or '<missing>'}, expected {target_sha}"
        )

    if not _traffic_is_reconciled(service, latest_revision):
        _run_gcloud_ok(
            [
                "run",
                "services",
                "update-traffic",
                ctx.service_name,
                "--project",
                ctx.project_id,
                "--region",
                ctx.region,
                "--to-revisions",
                f"{latest_revision}=100",
                "--quiet",
            ],
            run_gcloud=run_gcloud,
        )

    deadline = time.monotonic() + 300
    while True:
        service = _describe_service(ctx, run_gcloud=run_gcloud)
        if _traffic_is_reconciled(service, latest_revision):
            print(
                f"Cloud Run service {ctx.service_name} traffic reconciled to {latest_revision}."
            )
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Timed out waiting for {ctx.service_name} traffic to converge on {latest_revision}"
            )
        time.sleep(5)


def _legacy_scheduler_jobs(service_name: str) -> list[str]:
    candidates = [f"{service_name}-session-check-scheduler"]
    alias = service_name.removesuffix("-service")
    if alias and alias != service_name:
        candidates.extend(
            [
                f"{alias}-session-check-scheduler",
                f"{alias}-probe-scheduler",
                f"{alias}-precheck-scheduler",
            ]
        )
    candidates.append("firstrade-monitor-dispatcher-scheduler")
    seen: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.append(candidate)
    return seen


def cleanup_legacy_scheduler_jobs(
    env: Mapping[str, str] = os.environ,
    *,
    run_gcloud: RunGcloud = _run_gcloud,
) -> None:
    ctx, plan = _resolve_context(env)
    deleted: list[str] = []
    legacy_jobs = _legacy_scheduler_jobs(ctx.service_name)
    dispatcher_job = "firstrade-monitor-dispatcher-scheduler"
    direct_jobs = (
        f"{ctx.service_name}-probe-scheduler",
        f"{ctx.service_name}-precheck-scheduler",
    )
    targets = plan.get("targets")
    has_single_sync_target = not str(env.get("SYNC_PLAN_JSON", "") or "").strip() or (
        isinstance(targets, list) and len(targets) == 1
    )
    migration_confirmed = (
        str(env.get("DIRECT_MONITOR_MIGRATION_COMPLETE") or "").strip() == "true"
    )
    current_sync_confirmed = (
        str(env.get("DIRECT_MONITOR_SCHEDULERS_RECONCILED") or "").strip().lower()
        == "true"
    )
    direct_jobs_exist = (
        migration_confirmed
        and current_sync_confirmed
        and has_single_sync_target
        and all(
            run_gcloud(
                [
                    "scheduler",
                    "jobs",
                    "describe",
                    job_name,
                    "--project",
                    ctx.project_id,
                    "--location",
                    ctx.scheduler_location,
                ]
            ).returncode
            == 0
            for job_name in direct_jobs
        )
    )
    if dispatcher_job in legacy_jobs and not direct_jobs_exist:
        legacy_jobs.remove(dispatcher_job)
        print(
            f"Keeping legacy Cloud Scheduler job {dispatcher_job} until direct monitor jobs exist."
        )

    for job_name in legacy_jobs:
        result = run_gcloud(
            [
                "scheduler",
                "jobs",
                "describe",
                job_name,
                "--project",
                ctx.project_id,
                "--location",
                ctx.scheduler_location,
            ]
        )
        if result.returncode != 0:
            continue
        _run_gcloud_ok(
            [
                "scheduler",
                "jobs",
                "delete",
                job_name,
                "--project",
                ctx.project_id,
                "--location",
                ctx.scheduler_location,
                "--quiet",
            ],
            run_gcloud=run_gcloud,
        )
        deleted.append(job_name)

    if deleted:
        print(
            "Deleted legacy Cloud Scheduler job(s): " + ", ".join(deleted)
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("traffic", help="reconcile Cloud Run traffic")
    subparsers.add_parser(
        "scheduler-cleanup", help="delete explicit legacy scheduler jobs"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "traffic":
            reconcile_traffic()
        elif args.command == "scheduler-cleanup":
            cleanup_legacy_scheduler_jobs()
        else:  # pragma: no cover - argparse enforces subcommands
            raise ValueError(f"Unknown command: {args.command}")
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
