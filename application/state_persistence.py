"""Lightweight runtime state persistence.

The service intentionally avoids heavyweight Google client libraries. Cloud Run
already has a metadata server token, and the JSON APIs are enough for small
session and account-state payloads.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote, urlencode

import requests

METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)


class StatePersistenceError(RuntimeError):
    """Raised when the configured state store cannot read or write."""


def _normalize_prefix(prefix: str | None) -> str:
    return str(prefix or "").strip("/")


@dataclass(frozen=True)
class GcsStateStore:
    bucket: str
    prefix: str = "firstrade-platform"
    timeout_seconds: float = 10.0
    token_getter: Callable[[], str] | None = None

    def __post_init__(self) -> None:
        if not str(self.bucket or "").strip():
            raise ValueError("GCS state bucket must be non-empty.")

    def read_json(self, key: str) -> dict[str, Any] | None:
        object_name = self._object_name(key)
        url = (
            f"https://storage.googleapis.com/storage/v1/b/{quote(self.bucket, safe='')}/o/"
            f"{quote(object_name, safe='')}?alt=media"
        )
        response = requests.get(
            url,
            headers=self._auth_headers(),
            timeout=self.timeout_seconds,
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise StatePersistenceError(
                f"GCS read failed for {object_name}: HTTP {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise StatePersistenceError(f"GCS payload for {object_name} is not a JSON object.")
        return payload

    def write_json(self, key: str, payload: dict[str, Any]) -> bool:
        object_name = self._object_name(key)
        query = urlencode({"uploadType": "media", "name": object_name})
        url = f"https://storage.googleapis.com/upload/storage/v1/b/{quote(self.bucket, safe='')}/o?{query}"
        response = requests.post(
            url,
            headers={
                **self._auth_headers(),
                "Content-Type": "application/json; charset=utf-8",
            },
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            timeout=self.timeout_seconds,
        )
        if response.status_code not in (200, 201):
            raise StatePersistenceError(
                f"GCS write failed for {object_name}: HTTP {response.status_code}"
            )
        return True

    def _object_name(self, key: str) -> str:
        clean_key = str(key or "").strip("/")
        if not clean_key:
            raise ValueError("State key must be non-empty.")
        prefix = _normalize_prefix(self.prefix)
        return f"{prefix}/{clean_key}" if prefix else clean_key

    def _auth_headers(self) -> dict[str, str]:
        token = self.token_getter() if self.token_getter is not None else _metadata_access_token()
        return {"Authorization": f"Bearer {token}"}


def _metadata_access_token() -> str:
    response = requests.get(
        METADATA_TOKEN_URL,
        headers={"Metadata-Flavor": "Google"},
        timeout=3.0,
    )
    if response.status_code != 200:
        raise StatePersistenceError(f"Metadata token request failed: HTTP {response.status_code}")
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise StatePersistenceError("Metadata token response did not include access_token.")
    return str(token)


def build_gcs_state_store_from_env(
    env: Callable[[str, str | None], str | None] = os.getenv,
) -> GcsStateStore | None:
    bucket = (env("FIRSTRADE_GCS_STATE_BUCKET", "") or "").strip()
    if not bucket:
        return None
    return GcsStateStore(
        bucket=bucket,
        prefix=env("FIRSTRADE_STATE_PREFIX", "firstrade-platform") or "firstrade-platform",
    )
