"""Preserve one unqualified native history page in the runtime's private bucket."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


urlopen = build_opener(NoRedirect()).open


class ArchiveUnavailable(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def page_envelope(raw, *, account, start, end, source, observed_at):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 2 * 1024 * 1024:
        raise ArchiveUnavailable("archive_native_response_unavailable")
    manifest = {
        "schema_version": "firstrade.account_history_page.v1",
        "account_identity_sha256": hashlib.sha256(account.encode()).hexdigest(),
        "account_selector_verified": True,
        "request": {"from": start, "to": end, "page": 1, "per_page": 1000},
        "collector_source_commit": source, "retrieved_at": observed_at,
        "response_sha256": hashlib.sha256(raw).hexdigest(), "response_bytes": len(raw),
        "coverage": "first_page_only_not_complete_history",
        "transaction_qualification": "not_qualified",
    }
    body = json.dumps({"manifest": manifest,
        "response_body_base64": base64.b64encode(raw).decode("ascii")},
        separators=(",", ":"), allow_nan=False).encode()
    return body, {"native_response_sha256": manifest["response_sha256"],
                  "native_response_bytes": len(raw)}


def native_page_from_archive(body, *, account, start, end):
    try:
        envelope = json.loads(body)
        manifest = envelope["manifest"]
        if (manifest["schema_version"] != "firstrade.account_history_page.v1"
                or manifest["account_identity_sha256"] != hashlib.sha256(account.encode()).hexdigest()
                or manifest["account_selector_verified"] is not True
                or manifest["request"] != {"from": start, "to": end, "page": 1, "per_page": 1000}
                or manifest["coverage"] != "first_page_only_not_complete_history"
                or manifest["transaction_qualification"] != "not_qualified"):
            raise ValueError("manifest_mismatch")
    except Exception:
        raise ArchiveUnavailable("archive_manifest_invalid") from None
    try:
        raw = base64.b64decode(envelope["response_body_base64"], validate=True)
        if (not 0 < len(raw) <= 2 * 1024 * 1024 or len(raw) != manifest["response_bytes"]
                or hashlib.sha256(raw).hexdigest() != manifest["response_sha256"]):
            raise ValueError("native_integrity_mismatch")
        return json.loads(raw)
    except Exception:
        raise ArchiveUnavailable("archive_native_integrity_failed") from None


class PrivatePageArchive:
    def __init__(self, bucket):
        credential = subprocess.run(["gcloud", "auth", "print-access-token", "--quiet"],
            capture_output=True, text=True, timeout=30)
        if credential.returncode or not credential.stdout.strip():
            raise ArchiveUnavailable("archive_identity_unavailable")
        self._token = credential.stdout.strip()
        self._bucket = quote(bucket, safe="")
        self._objects = "https://storage.googleapis.com/storage/v1/b/" + self._bucket + "/o"
        try:
            metadata = json.loads(self._request(self._objects[:-2], limit=65536))
        except Exception:
            raise ArchiveUnavailable("archive_bucket_metadata_unavailable") from None
        iam = metadata.get("iamConfiguration", {})
        if (iam.get("publicAccessPrevention") != "enforced"
                or iam.get("uniformBucketLevelAccess", {}).get("enabled") is not True):
            raise ArchiveUnavailable("archive_bucket_not_private")

    def _request(self, url, *, body=None, limit=4 * 1024 * 1024):
        request = Request(url, data=body, headers={"Authorization": "Bearer " + self._token,
                          "Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            result = response.read(limit + 1)
        if len(result) > limit:
            raise ValueError("archive_response_size_exceeded")
        return result

    def create(self, name, body):
        url = "https://storage.googleapis.com/upload/storage/v1/b/" + self._bucket + "/o?"
        url += urlencode({"uploadType": "media", "name": name, "ifGenerationMatch": 0})
        try:
            metadata = json.loads(self._request(url, body=body, limit=65536))
            generation = metadata["generation"]
            if not isinstance(generation, str) or not re.fullmatch(r"[1-9][0-9]{0,24}", generation):
                raise ValueError("archive_generation_invalid")
        except HTTPError as error:
            reason = {412: "archive_object_already_exists", 403: "archive_write_denied"}.get(
                error.code, "archive_write_outcome_unknown")
            raise ArchiveUnavailable(reason) from None
        except Exception:
            # A POST may have committed before a lost response. Never retry it.
            raise ArchiveUnavailable("archive_write_outcome_unknown") from None
        try:
            saved = self._request(self._objects + "/" + quote(name, safe="") + "?"
                + urlencode({"alt": "media", "generation": generation}))
            if saved != body:
                raise ValueError("archive_hash_mismatch")
        except Exception:
            raise ArchiveUnavailable("archive_readback_failed") from None
        return {"archive_verified": True, "archive_generation": generation,
            "archive_sha256": hashlib.sha256(body).hexdigest(), "archive_write_requests": 1}

    def read_pinned(self, name, generation, expected_sha256):
        if (not re.fullmatch(r"[1-9][0-9]{0,24}", generation)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)):
            raise ArchiveUnavailable("archive_selector_invalid")
        try:
            body = self._request(self._objects + "/" + quote(name, safe="") + "?"
                + urlencode({"alt": "media", "generation": generation}))
        except Exception:
            raise ArchiveUnavailable("archive_read_failed") from None
        if hashlib.sha256(body).hexdigest() != expected_sha256:
            raise ArchiveUnavailable("archive_integrity_failed")
        return body
