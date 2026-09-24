#!/usr/bin/env python3
"""Reject an unapproved UES dependency change before building a live image.

The currently serving revision is the continuity baseline. This does not
approve a new strategy release; such a change needs a separate release path.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path


UES = "us-equity-strategies"
SHA = re.compile(r"^[0-9a-f]{40}$")


class ContinuityError(ValueError):
    pass


def _run(*args: str) -> str:
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if result.returncode:
        raise ContinuityError(f"Unable to read production baseline ({args[0]} failed)")
    return result.stdout


def _ues_lock_revision(data: str) -> str:
    packages = tomllib.loads(data).get("package", [])
    matches = [package for package in packages if package.get("name") == UES]
    if len(matches) != 1:
        raise ContinuityError("Exactly one locked UES package is required")
    source = matches[0].get("source") or {}
    match = re.search(r"[?&]rev=([0-9a-f]{40})#([0-9a-f]{40})$", str(source.get("git") or ""))
    if not match or match.group(1) != match.group(2):
        raise ContinuityError("Locked UES package needs an immutable full revision")
    return match.group(1)


def _ues_project_revision(data: str) -> str:
    dependencies = tomllib.loads(data).get("project", {}).get("dependencies", [])
    matches = [item for item in dependencies if item.lower().startswith(f"{UES} @ ")]
    if len(matches) != 1:
        raise ContinuityError("Exactly one direct UES dependency is required")
    revision = matches[0].rsplit("@", 1)[-1]
    if not SHA.fullmatch(revision):
        raise ContinuityError("Direct UES dependency needs an immutable full revision")
    return revision


def _active_commit(service: str, project: str, region: str) -> str:
    service_json = json.loads(
        _run(
            "gcloud", "run", "services", "describe", service,
            f"--project={project}", f"--region={region}", "--format=json",
        )
    )
    traffic = service_json.get("status", {}).get("traffic") or []
    active = [
        item for item in traffic
        if isinstance(item, Mapping) and int(item.get("percent") or 0) > 0
    ]
    if len(active) != 1 or int(active[0]["percent"]) != 100:
        raise ContinuityError("A single 100% serving revision is required")
    revision = str(active[0].get("revisionName") or "")
    if not revision:
        raise ContinuityError("Serving revision name is missing")
    revision_json = json.loads(
        _run(
            "gcloud", "run", "revisions", "describe", revision,
            f"--project={project}", f"--region={region}", "--format=json",
        )
    )
    commit = str(revision_json.get("metadata", {}).get("labels", {}).get("commit-sha") or "")
    if not SHA.fullmatch(commit):
        raise ContinuityError("Serving revision has no full commit-sha label")
    return commit


def verify(service: str, project: str, region: str) -> None:
    active_commit = _active_commit(service, project, region)
    candidate_lock = _ues_lock_revision(Path("uv.lock").read_text(encoding="utf-8"))
    candidate_project = _ues_project_revision(Path("pyproject.toml").read_text(encoding="utf-8"))
    candidate_qsl = tomllib.loads(Path("qsl.toml").read_text(encoding="utf-8")).get("qsl", {}).get("requires", {}).get("us_equity_strategies")
    if candidate_lock != candidate_project or candidate_qsl != candidate_lock:
        raise ContinuityError("Candidate UES lock, direct dependency, and QSL contract disagree")
    _run("git", "fetch", "--no-tags", "--depth=1", "origin", active_commit)
    active_lock = _ues_lock_revision(_run("git", "show", f"{active_commit}:uv.lock"))
    active_project = _ues_project_revision(_run("git", "show", f"{active_commit}:pyproject.toml"))
    active_qsl = tomllib.loads(_run("git", "show", f"{active_commit}:qsl.toml")).get("qsl", {}).get("requires", {}).get("us_equity_strategies")
    if active_lock != active_project or active_qsl != active_lock:
        raise ContinuityError("Serving revision UES lock, direct dependency, and QSL contract disagree")
    if candidate_lock != active_lock:
        raise ContinuityError("Candidate UES revision differs from the serving production revision")
    print("Verified production UES dependency continuity")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    args = parser.parse_args()
    try:
        verify(args.service, args.project, args.region)
    except (ContinuityError, OSError, ValueError, KeyError) as exc:
        print(f"Production UES continuity check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
