"""Lightweight runtime state persistence using quant_platform_kit.cloud object store."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable


class StatePersistenceError(RuntimeError):
    """Raised when the configured state store cannot read or write."""


def _normalize_prefix(prefix: str | None) -> str:
    return str(prefix or "").strip("/")


def _object_store():
    try:
        from quant_platform_kit.cloud import get_object_store

        return get_object_store()
    except ImportError:
        raise StatePersistenceError("quant_platform_kit.cloud not available")


@dataclass(frozen=True)
class GcsStateStore:
    bucket: str
    prefix: str = "firstrade-platform"
    client_factory: Callable[..., Any] | None = None

    def __post_init__(self) -> None:
        if not str(self.bucket or "").strip():
            raise ValueError("GCS state bucket must be non-empty.")

    def _object_uri(self, key: str) -> str:
        clean_key = str(key or "").strip("/")
        if not clean_key:
            raise ValueError("State key must be non-empty.")
        prefix = _normalize_prefix(self.prefix)
        object_name = f"{prefix}/{clean_key}" if prefix else clean_key
        return f"gs://{self.bucket}/{object_name}"

    def read_json(self, key: str) -> dict[str, Any] | None:
        uri = self._object_uri(key)
        try:
            text = _object_store().read_text(uri)
        except Exception as exc:
            error_msg = str(exc)
            if "404" in error_msg or "NOT_FOUND" in error_msg.upper():
                return None
            raise StatePersistenceError(f"GCS read failed for {key}: {exc}") from exc
        if text is None:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StatePersistenceError(f"GCS payload for {key} is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise StatePersistenceError(f"GCS payload for {key} is not a JSON object.")
        return payload

    def write_json(self, key: str, payload: dict[str, Any]) -> bool:
        uri = self._object_uri(key)
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            _object_store().write_text(uri, data)
        except Exception as exc:
            raise StatePersistenceError(f"GCS write failed for {key}: {exc}") from exc
        return True

    def create_json(self, key: str, payload: dict[str, Any]) -> bool:
        """Atomically create a JSON object; return False if it already exists."""
        uri = self._object_uri(key)
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        bucket_name, separator, object_name = uri[5:].partition("/")
        if not separator or not bucket_name or not object_name:
            raise StatePersistenceError(f"Invalid GCS state URI for {key}")
        try:
            from google.api_core.exceptions import Conflict, PreconditionFailed
            from google.cloud import storage

            factory = self.client_factory or storage.Client
            try:
                client = factory()
            except TypeError:
                client = factory(project=None)
            blob = client.bucket(bucket_name).blob(object_name)
            blob.upload_from_string(
                data,
                content_type="application/json",
                if_generation_match=0,
            )
            return True
        except (Conflict, PreconditionFailed):
            return False
        except Exception as exc:
            raise StatePersistenceError(f"GCS atomic create failed for {key}: {exc}") from exc


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
