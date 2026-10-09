"""Project and publish one cached-only Firstrade account-facts snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from application.account_facts_readonly import AccountFactsUnavailable

_APPROVED_SYNC_URL = "https://qsl-strategy-switch-console.pigbibi.workers.dev/api/account-facts/sync"
_TARGET_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", re.ASCII)
_ACCOUNT_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*", re.ASCII)
_ACCOUNT_SCOPE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*", re.ASCII)
_BINDING_ID_RE = re.compile(r"[a-f0-9]{64}", re.ASCII)
_BROKER_ACCOUNT_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)
_CURRENCY_RE = re.compile(r"[A-Z]{3}", re.ASCII)
_AMOUNT_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", re.ASCII)


class AccountFactsPublishError(RuntimeError):
    """Fixed safe failure for account-facts configuration or transport."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class AccountFactsPublishConfig:
    sync_url: str
    sync_token: str = field(repr=False)
    target_id: str = field(repr=False)
    source_binding_id: str = field(repr=False)
    account_key: str = field(repr=False)
    account_scope: str = field(repr=False)
    account_id: str | None = field(repr=False)


def _required_env(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise AccountFactsPublishError("configuration_incomplete")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise AccountFactsPublishError("configuration_invalid")
    return value


def load_account_facts_publish_config(
    env: Mapping[str, str],
    *,
    expected_account_id: str | None = None,
    allow_missing_account: bool = False,
) -> AccountFactsPublishConfig:
    if env.get("FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED") != "true":
        raise AccountFactsPublishError("disabled")

    sync_url = _required_env(env, "FIRSTRADE_ACCOUNT_FACTS_SYNC_URL")
    try:
        parsed = urlsplit(sync_url)
    except ValueError:
        raise AccountFactsPublishError("destination_not_approved") from None
    if (
        sync_url != _APPROVED_SYNC_URL
        or parsed.scheme != "https"
        or parsed.netloc != "qsl-strategy-switch-console.pigbibi.workers.dev"
        or parsed.path != "/api/account-facts/sync"
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise AccountFactsPublishError("destination_not_approved")

    token = _required_env(env, "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN")
    if any(char.isspace() for char in token):
        raise AccountFactsPublishError("sync_token_invalid")
    if any(
        token == env.get(other)
        for other in (
            "ACCOUNT_FACTS_SYNC_TOKEN",
            "IBKR_ACCOUNT_FACTS_SYNC_TOKEN",
            "SCHWAB_ACCOUNT_FACTS_SYNC_TOKEN",
            "BINANCE_ACCOUNT_FACTS_SYNC_TOKEN",
            "EXECUTION_EVIDENCE_SYNC_TOKEN",
            "STRATEGY_SWITCH_SYNC_TOKEN",
            "RECONCILIATION_RECOVERY_SYNC_TOKEN",
            "RECONCILIATION_RECOVERY_CONTROLLER_TOKEN",
        )
    ):
        raise AccountFactsPublishError("sync_token_not_dedicated")

    target_id = _required_env(env, "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID")
    if not _TARGET_ID_RE.fullmatch(target_id):
        raise AccountFactsPublishError("target_invalid")
    source_binding_id = _required_env(env, "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID")
    if not _BINDING_ID_RE.fullmatch(source_binding_id):
        raise AccountFactsPublishError("source_binding_invalid")
    account_key = _required_env(env, "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY")
    if len(account_key) > 128 or not _ACCOUNT_KEY_RE.fullmatch(account_key):
        raise AccountFactsPublishError("account_key_invalid")
    account_scope = _required_env(env, "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE")
    if len(account_scope) > 128 or not _ACCOUNT_SCOPE_RE.fullmatch(account_scope):
        raise AccountFactsPublishError("account_scope_invalid")
    account_id = expected_account_id
    configured_account_id = env.get("FIRSTRADE_ACCOUNT")
    if configured_account_id:
        configured_account_id = _required_env(env, "FIRSTRADE_ACCOUNT")
        if account_id is not None and configured_account_id != account_id:
            raise AccountFactsPublishError("account_identity_mismatch")
        account_id = configured_account_id
    if account_id is None and not allow_missing_account:
        raise AccountFactsPublishError("account_identity_unavailable")
    if account_id is not None and (
        not isinstance(account_id, str) or not _BROKER_ACCOUNT_ID_RE.fullmatch(account_id)
    ):
        raise AccountFactsPublishError("account_identity_invalid")

    return AccountFactsPublishConfig(
        sync_url=sync_url,
        sync_token=token,
        target_id=target_id,
        source_binding_id=source_binding_id,
        account_key=account_key,
        account_scope=account_scope,
        account_id=account_id,
    )


def bind_account_facts_runtime_selector(
    config: AccountFactsPublishConfig,
    runtime_account_id: object,
) -> AccountFactsPublishConfig:
    if not isinstance(runtime_account_id, str) or not _BROKER_ACCOUNT_ID_RE.fullmatch(runtime_account_id):
        raise AccountFactsPublishError("runtime_target_account_selector_invalid")
    if config.account_id is not None and config.account_id != runtime_account_id:
        raise AccountFactsPublishError("account_identity_mismatch")
    return replace(config, account_id=runtime_account_id)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise AccountFactsPublishError("snapshot_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AccountFactsPublishError("snapshot_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AccountFactsPublishError("snapshot_invalid")
    return parsed.astimezone(timezone.utc)


def _amount(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64 or not _AMOUNT_RE.fullmatch(value):
        raise AccountFactsPublishError("snapshot_amount_invalid")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise AccountFactsPublishError("snapshot_amount_invalid") from None
    if not number.is_finite():
        raise AccountFactsPublishError("snapshot_amount_invalid")
    integer, _, fraction = value.lstrip("-").partition(".")
    if len(integer) > 15 or len(fraction) > 8:
        raise AccountFactsPublishError("snapshot_amount_out_of_range")
    return value


def build_firstrade_account_snapshot(
    observation: Mapping[str, Any],
    config: AccountFactsPublishConfig,
    *,
    account_scope: str,
) -> dict[str, Any]:
    """Build the fixed QRS history body without positions or derived values."""
    if (
        observation.get("status") != "available"
        or observation.get("platform") != "firstrade"
        or observation.get("account_selector_status") != "matched"
        or config.account_id is None
        or observation.get("broker_account_id") != config.account_id
        or account_scope != config.account_scope
    ):
        raise AccountFactsPublishError("account_identity_mismatch")
    balances = observation.get("balances")
    if not isinstance(balances, Mapping):
        raise AccountFactsPublishError("snapshot_invalid")
    currency = balances.get("currency")
    # Firstrade US equity balance payloads often omit an ISO currency field; the
    # rest of this platform already treats Firstrade cash as USD.
    if currency is None:
        currency = "USD"
    if not isinstance(currency, str) or not _CURRENCY_RE.fullmatch(currency):
        raise AccountFactsPublishError("provider_currency_unavailable")
    provider_equity = balances.get("provider_equity")
    if provider_equity is None:
        raise AccountFactsPublishError("provider_equity_unavailable")

    started_at = _timestamp(observation.get("observed_started_at"))
    finished_at = _timestamp(observation.get("observed_finished_at"))
    if finished_at < started_at:
        raise AccountFactsPublishError("snapshot_time_invalid")

    cash_balance = balances.get("cash_balance")
    cash: list[dict[str, str]] = []
    if cash_balance is not None:
        cash.append({
            "currency": currency,
            "cash_balance": _amount(cash_balance),
            "source_tag": "provider.cash_balance",
        })

    return {
        "schema_version": "firstrade_account_snapshot_history.v1",
        "snapshot_schema_version": "firstrade_account_snapshot.v1",
        "account_scope": config.account_scope,
        "target_id": config.target_id,
        "source_binding": {
            "kind": "deployment_runtime_account",
            "status": "bound",
            "id": config.source_binding_id,
        },
        "observed_started_at": started_at.isoformat(),
        "observed_finished_at": finished_at.isoformat(),
        "snapshot_atomic": False,
        "observation_date": started_at.date().isoformat(),
        "broker_account_id": config.account_id,
        "broker_reported_balances": [{
            "currency": currency,
            "net_assets": _amount(provider_equity),
        }],
        "cash": cash,
    }


def _ack_matches(
    payload: object,
    *,
    account_key: str,
    target_id: str,
    observation_date: str,
    observed_finished_at: str,
) -> bool:
    if not isinstance(payload, Mapping):
        return False

    def contains_private_account_id(value: object) -> bool:
        if isinstance(value, Mapping):
            return any(
                key == "broker_account_id" or contains_private_account_id(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(contains_private_account_id(item) for item in value)
        return False

    return (
        payload.get("ok") is True
        and payload.get("stored") is True
        and payload.get("platform") == "firstrade"
        and payload.get("account_key") == account_key
        and payload.get("target_id") == target_id
        and payload.get("observation_date") == observation_date
        and payload.get("observed_finished_at") == observed_finished_at
        and not contains_private_account_id(payload)
    )


def publish_firstrade_account_snapshot(
    payload: Mapping[str, Any],
    config: AccountFactsPublishConfig,
    *,
    session_factory: Callable[[], Any] = requests.Session,
) -> None:
    """POST once to the fixed QRS endpoint and require its exact safe ACK."""
    try:
        session = session_factory()
    except Exception:
        raise AccountFactsPublishError("sync_unavailable") from None
    try:
        session.trust_env = False
        try:
            response = session.post(
                config.sync_url,
                json=dict(payload),
                headers={"Authorization": f"Bearer {config.sync_token}"},
                timeout=(5, 15),
                allow_redirects=False,
            )
        except Exception:
            raise AccountFactsPublishError("sync_unavailable") from None
        if getattr(response, "status_code", None) != 200:
            raise AccountFactsPublishError("sync_rejected")
        response_body = getattr(response, "content", None)
        if isinstance(response_body, (bytes, bytearray)) and len(response_body) > 8192:
            raise AccountFactsPublishError("sync_ack_invalid")
        try:
            acknowledgement = response.json()
        except Exception:
            raise AccountFactsPublishError("sync_ack_invalid") from None
        if not _ack_matches(
            acknowledgement,
            account_key=config.account_key,
            target_id=config.target_id,
            observation_date=str(payload["observation_date"]),
            observed_finished_at=str(payload["observed_finished_at"]),
        ):
            raise AccountFactsPublishError("sync_ack_invalid")
    finally:
        try:
            session.close()
        except Exception:
            pass
