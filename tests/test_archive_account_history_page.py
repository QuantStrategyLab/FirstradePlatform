import base64
import hashlib
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from scripts import archive_account_history_page as archive


def test_archive_keeps_exact_native_bytes_and_marks_page_unqualified():
    raw = b'{"items":[{"amount":1.2300,"private":"synthetic-value"}]}'
    body, summary = archive.page_envelope(raw, account="synthetic-account",
        start="2026-09-01", end="2026-09-30", source="a" * 40,
        observed_at="2026-10-27T09:00:00+00:00")
    envelope = json.loads(body)
    assert base64.b64decode(envelope["response_body_base64"]) == raw
    assert envelope["manifest"]["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert envelope["manifest"]["coverage"] == "first_page_only_not_complete_history"
    assert envelope["manifest"]["transaction_qualification"] == "not_qualified"
    assert "synthetic-value" not in json.dumps(summary)
    assert "synthetic-account" not in body.decode()


class Response:
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def read(self, _limit): return self.value


def test_create_only_archive_uses_stdin_credential_and_generation_pinned_readback(monkeypatch):
    calls = []
    payload = b'{"synthetic":"private"}'
    def run(args, **kwargs):
        assert args == ["gcloud", "auth", "print-access-token", "--quiet"]
        assert kwargs["capture_output"]
        return SimpleNamespace(returncode=0, stdout="synthetic-secret-token")
    def open_request(request, **kwargs):
        calls.append(request)
        assert request.get_header("Authorization") == "Bearer synthetic-secret-token"
        if len(calls) == 1:
            assert request.get_method() == "GET"
            return Response(json.dumps({"iamConfiguration": {
                "publicAccessPrevention": "enforced", "uniformBucketLevelAccess": {"enabled": True}}}).encode())
        if len(calls) == 2:
            assert request.get_method() == "POST" and request.data == payload
            assert "ifGenerationMatch=0" in request.full_url
            return Response(b'{"generation":"123"}')
        assert request.get_method() == "GET" and "generation=123" in request.full_url
        return Response(payload)
    monkeypatch.setattr(archive.subprocess, "run", run)
    monkeypatch.setattr(archive, "urlopen", open_request)
    store = archive.PrivatePageArchive("synthetic-bucket")
    result = store.create("synthetic-prefix/page.json", payload)
    assert result == {"archive_verified": True, "archive_generation": "123",
        "archive_sha256": hashlib.sha256(payload).hexdigest(), "archive_write_requests": 1}
    assert len(calls) == 3 and "synthetic-secret-token" not in json.dumps(result)


@pytest.mark.parametrize("failure", ["write_unknown", "collision", "read_mismatch"])
def test_archive_failures_never_retry_or_overwrite(monkeypatch, failure):
    calls = []
    monkeypatch.setattr(archive.subprocess, "run", lambda *_a, **_k:
        SimpleNamespace(returncode=0, stdout="synthetic-secret-token"))
    def request(req, **_kwargs):
        calls.append(req)
        if len(calls) == 1:
            return Response(b'{"iamConfiguration":{"publicAccessPrevention":"enforced", "uniformBucketLevelAccess":{"enabled":true}}}')
        if len(calls) == 2:
            if failure == "write_unknown": raise TimeoutError("private diagnostic")
            if failure == "collision": raise HTTPError(req.full_url, 412, "private diagnostic", {}, None)
            return Response(b'{"generation":"123"}')
        return Response(b'"different private response"')
    monkeypatch.setattr(archive, "urlopen", request)
    store = archive.PrivatePageArchive("synthetic-bucket")
    with pytest.raises(archive.ArchiveUnavailable) as caught:
        store.create("page.json", b'"private original"')
    assert len(calls) == (3 if failure == "read_mismatch" else 2)
    assert caught.value.reason == {"write_unknown": "archive_write_outcome_unknown",
        "collision": "archive_object_already_exists", "read_mismatch": "archive_readback_failed"}[failure]
    assert "private" not in str(caught.value)


def test_public_or_nonuniform_bucket_stops_before_broker_or_write(monkeypatch):
    monkeypatch.setattr(archive.subprocess, "run", lambda *_a, **_k:
        SimpleNamespace(returncode=0, stdout="synthetic-secret-token"))
    monkeypatch.setattr(archive, "urlopen", lambda *_a, **_k:
        Response(b'{"iamConfiguration":{"publicAccessPrevention":"inherited"}}'))
    with pytest.raises(archive.ArchiveUnavailable, match="archive_bucket_not_private"):
        archive.PrivatePageArchive("synthetic-bucket")


def test_google_credential_is_not_forwarded_to_redirect():
    handler = archive.NoRedirect()
    assert handler.redirect_request(None, None, 302, "found", {}, "https://synthetic.invalid") is None


def test_native_archive_reconstruction_preserves_bytes_and_checks_account_window():
    raw = b'{"items":[{"amount":1.2300}]}'
    body, _ = archive.page_envelope(raw, account="synthetic-account", start="2026-09-01",
        end="2026-09-30", source="a" * 40, observed_at="2026-10-27T10:00:00Z")
    assert archive.native_page_from_archive(body, account="synthetic-account",
        start="2026-09-01", end="2026-09-30") == json.loads(raw)
    with pytest.raises(archive.ArchiveUnavailable, match="archive_manifest_invalid"):
        archive.native_page_from_archive(body, account="different-account",
            start="2026-09-01", end="2026-09-30")
    with pytest.raises(archive.ArchiveUnavailable, match="archive_manifest_invalid"):
        archive.native_page_from_archive(body, account="synthetic-account",
            start="2026-09-02", end="2026-09-30")
    envelope = json.loads(body)
    envelope["response_body_base64"] = base64.b64encode(b'"different private body"').decode()
    with pytest.raises(archive.ArchiveUnavailable, match="archive_native_integrity_failed"):
        archive.native_page_from_archive(json.dumps(envelope).encode(), account="synthetic-account",
            start="2026-09-01", end="2026-09-30")


def test_pinned_archive_read_checks_receipt_hash_and_never_posts(monkeypatch):
    store = archive.PrivatePageArchive.__new__(archive.PrivatePageArchive)
    store._objects = "https://storage.googleapis.com/storage/v1/b/synthetic/o"
    calls = []
    def read(url):
        calls.append(url)
        return b'"private body"'
    monkeypatch.setattr(store, "_request", read)
    expected = hashlib.sha256(b'"private body"').hexdigest()
    assert store.read_pinned("page.json", "123", expected) == b'"private body"'
    assert "generation=123" in calls[0] and "alt=media" in calls[0]
    with pytest.raises(archive.ArchiveUnavailable, match="archive_integrity_failed"):
        store.read_pinned("page.json", "123", "a" * 64)
    with pytest.raises(archive.ArchiveUnavailable, match="archive_selector_invalid"):
        store.read_pinned("page.json", "private-value", expected)
    assert len(calls) == 2
