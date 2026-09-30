from __future__ import annotations

import json
import urllib.error
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self

import pytest

from scripts import inspect_account_data_readiness as readiness

PROJECT = "projects/firstradequant/locations/us-central1/services/firstrade-platform"
REVISION = f"{PROJECT}/revisions/firstrade-platform-abc123"
COMMIT = "a" * 40
SENTINEL = "private-selector-value-do-not-print"


def _service() -> dict[str, Any]:
    return {
        "name": PROJECT,
        "etag": "etag-one",
        "reconciling": False,
        "generation": "7",
        "observedGeneration": "7",
        "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
        "conditions": [],
        "template": {
            "containers": [
                {"env": [{"name": "FIRSTRADE_ACCOUNT", "value": SENTINEL}]}
            ]
        },
        "trafficStatuses": [{"revision": REVISION, "percent": 100}],
    }


def _revision(*, env: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "name": REVISION,
        "reconciling": False,
        "generation": "3",
        "observedGeneration": "3",
        "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}],
        "labels": {"commit-sha": COMMIT},
        "containers": [{"env": env or []}],
    }


def _runner(
    service_responses: list[Mapping[str, Any]] | None = None,
    revision: Mapping[str, Any] | None = None,
) -> tuple[Any, list[str]]:
    queue = list(service_responses or [_service(), _service()])
    urls: list[str] = []

    def request(url: str, token: str) -> Mapping[str, Any]:
        assert token == "token"
        urls.append(url)
        if url.endswith("/revisions/firstrade-platform-abc123"):
            return revision or _revision()
        return queue.pop(0)

    return request, urls


def test_inspects_only_exact_project_region_service_and_serving_revision() -> None:
    request, urls = _runner(revision=_revision(env=[{"name": "FIRSTRADE_ACCOUNT", "value": SENTINEL}]))

    result = readiness.inspect_readiness(
        "firstrade-platform", "us-central1", "token", request_json=request
    )

    assert result == {
        "status": "verified",
        "reason": "metadata_only_verified",
        "http_status": None,
        "source_commit": COMMIT,
        "selector_configured": True,
        "selector_source": "literal",
    }
    assert urls == [
        f"{readiness.API_ROOT}/{PROJECT}",
        f"{readiness.API_ROOT}/{REVISION}",
        f"{readiness.API_ROOT}/{PROJECT}",
    ]
    assert all(url.startswith("https://run.googleapis.com/v2/projects/firstradequant/") for url in urls)


@pytest.mark.parametrize(
    ("env", "configured", "source"),
    [
        ([{"name": "FIRSTRADE_ACCOUNT", "valueSource": {"secretKeyRef": {"secret": SENTINEL}}}], True, "secret_reference"),
        ([{"name": "OTHER_SETTING", "value": SENTINEL}], False, "absent"),
        ([{"name": "FIRSTRADE_ACCOUNT", "value": "  "}], False, "absent"),
    ],
)
def test_selector_reports_only_presence_and_source(
    env: list[dict[str, Any]], configured: bool, source: str
) -> None:
    request, _ = _runner(revision=_revision(env=env))

    result = readiness.inspect_readiness(
        "firstrade-platform", "us-central1", "token", request_json=request
    )

    assert result["selector_configured"] is configured
    assert result["selector_source"] == source
    assert SENTINEL not in json.dumps(result)


@pytest.mark.parametrize(
    "traffic",
    [
        [],
        [{"revision": "rev-a", "percent": 0}],
        [{"revision": "rev-a", "percent": 50}, {"revision": "rev-b", "percent": 50}],
        [{"revision": "rev-a", "percent": 99}],
        [{"revision": "rev-a", "percent": True}],
    ],
)
def test_rejects_missing_ambiguous_or_invalid_actual_traffic(traffic: list[dict[str, Any]]) -> None:
    service = _service()
    service["trafficStatuses"] = traffic
    request, _ = _runner([service, service])

    with pytest.raises(readiness.DiagnosticFailure):
        readiness.inspect_readiness(
            "firstrade-platform", "us-central1", "token", request_json=request
        )


def test_accepts_protojson_zero_percent_default_without_guessing_revision() -> None:
    service = _service()
    service.pop("reconciling")
    service["trafficStatuses"] = [
        {"revision": "firstrade-platform-zero", "tag": "preview"},
        {"revision": REVISION, "percent": 100},
    ]
    revision = _revision()
    revision.pop("reconciling")
    request, _ = _runner([service, service], revision=revision)

    result = readiness.inspect_readiness(
        "firstrade-platform", "us-central1", "token", request_json=request
    )

    assert result["status"] == "verified"


def test_rejects_service_change_during_metadata_read() -> None:
    before = _service()
    after = _service()
    after["etag"] = "etag-two"
    request, _ = _runner([before, after])

    with pytest.raises(readiness.DiagnosticFailure, match="service_changed_during_read"):
        readiness.inspect_readiness(
            "firstrade-platform", "us-central1", "token", request_json=request
        )


def test_rejects_invalid_resource_parts_without_request() -> None:
    request, urls = _runner()

    with pytest.raises(readiness.DiagnosticFailure):
        readiness.inspect_readiness("service/other", "us-central1", "token", request_json=request)

    assert urls == []


def test_http_reader_uses_only_get_to_fixed_google_api_and_preserves_status(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            assert size == readiness.MAX_RESPONSE_BYTES + 1
            return b'{"name":"ok"}'

    class FakeOpener:
        def open(self, request: Any, timeout: int) -> FakeResponse:
            assert request.method == "GET"
            assert request.full_url.startswith("https://run.googleapis.com/v2/projects/firstradequant/")
            assert "SENTINEL" not in request.full_url
            assert timeout == 15
            return FakeResponse()

    monkeypatch.setattr(readiness.urllib.request, "build_opener", lambda *handlers: FakeOpener())
    assert readiness._request_json(f"{readiness.API_ROOT}/{PROJECT}", "token") == {"name": "ok"}

    def fail_open(self, request: Any, timeout: int) -> None:
        raise urllib.error.HTTPError(request.full_url, 403, SENTINEL, {}, None)

    monkeypatch.setattr(FakeOpener, "open", fail_open)
    with pytest.raises(readiness.DiagnosticFailure) as caught:
        readiness._request_json(f"{readiness.API_ROOT}/{PROJECT}", "token")
    assert caught.value.http_status == 403
    assert SENTINEL not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://run.googleapis.com/v2/projects/other/locations/us-central1/services/firstrade-platform",
        "https://example.test/v2/projects/firstradequant/locations/us-central1/services/firstrade-platform",
        f"{readiness.API_ROOT}/{PROJECT}?alt=json",
        f"{readiness.API_ROOT}/projects/firstradequant/locations/us-central1/services/firstrade-platform/revisions/",
    ],
)
def test_http_reader_rejects_non_target_uris_before_network(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    def no_request(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("unexpected network request")

    monkeypatch.setattr(readiness.urllib.request, "build_opener", no_request)
    with pytest.raises(readiness.DiagnosticFailure, match="request_target_rejected"):
        readiness._request_json(url, "token")


def test_http_reader_rejects_oversized_metadata_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class LargeResponse:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            assert size == readiness.MAX_RESPONSE_BYTES + 1
            return b"x" * size

    class FakeOpener:
        def open(self, request: Any, timeout: int) -> LargeResponse:
            return LargeResponse()

    monkeypatch.setattr(readiness.urllib.request, "build_opener", lambda *handlers: FakeOpener())
    with pytest.raises(readiness.DiagnosticFailure, match="metadata_response_too_large"):
        readiness._request_json(f"{readiness.API_ROOT}/{PROJECT}", "token")


def test_workflow_metadata_path_is_opt_in_and_isolated() -> None:
    workflow = Path(readiness.__file__).resolve().parents[1] / ".github/workflows/runtime-target-lifecycle.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "metadata_only:" in content
    assert "default: false" in content
    assert "github.event_name == 'workflow_dispatch' && inputs.metadata_only" in content
    assert "account_data_readiness:" in content
    assert "scripts/inspect_account_data_readiness.py" in content
    assert "workflow_run:" in content and 'cron: "37 * * * *"' in content
    assert "uv sync --frozen --no-dev" in content
    assert content.count("uv sync --frozen --no-dev") == 1
    assert "probe" not in content.split("account_data_readiness:", 1)[1].lower()
