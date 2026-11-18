"""Default-off, read-only Firstrade reconciliation evidence.

The collector never imports an order port or writes broker, session, baseline,
or execution state. Firstrade's available order reader does not establish a
bounded fill-history contract, so ``recent_executions`` is deliberately marked
unavailable and every candidate remains blocked until that provider boundary is
separately verified.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from quant_platform_kit.common.broker_reconciliation import (
    BrokerReconciliationEvidence,
    BrokerReconciliationFinding,
    build_broker_reconciliation_evidence,
    calculate_broker_observation_sha256,
    evaluate_broker_reconciliation_recovery,
)

FIRSTRADE_RECONCILIATION_ENABLED_ENV = "FIRSTRADE_BROKER_RECONCILIATION_ENABLED"
FIRSTRADE_RECONCILIATION_EXPECTED_DIGESTS_ENV = "FIRSTRADE_RECONCILIATION_EXPECTED_DIGESTS_JSON"
_EXPECTED_DIGEST_KEYS = (
    "account_scope_sha256",
    "positions_sha256",
    "cash_sha256",
    "open_orders_sha256",
    "recent_executions_sha256",
    "local_execution_ledger_sha256",
)
_SAFE_CANDIDATE_KEYS = frozenset(
    {
        "schema_version",
        "permits_active_lkg",
        "expected_digests_configured",
        "execution_ledger_records_count",
        "recent_executions_available",
        "local_execution_ledger_available",
        "recovery_blockers",
        "evidence",
    }
)
_TERMINAL_ORDER_STATUSES = frozenset({"CANCELED", "REJECTED", "EXPIRED", "FILLED", "REPLACED"})


class FirstradeReconciliationUnavailable(RuntimeError):
    """Raised when read-only reconciliation cannot safely produce evidence."""


def reconciliation_enabled(
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
) -> bool:
    """Require an explicit exact boolean; absent and malformed values stay off."""

    return _text(env_reader(FIRSTRADE_RECONCILIATION_ENABLED_ENV, None)).lower() == "true"


def _text(value: object) -> str:
    return str(value or "").strip()


def _json_value(value: object, *, surface: str) -> object:
    """Reject non-canonical broker payloads before they can affect a digest."""

    try:
        return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise FirstradeReconciliationUnavailable(
            f"Firstrade reconciliation received malformed {surface}."
        ) from exc


def _canonical_records(records: list[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    return tuple(
        sorted(
            (dict(_json_value(record, surface="orders")) for record in records),
            key=lambda record: json.dumps(record, ensure_ascii=True, sort_keys=True),
        )
    )


def _position_quantities(payload: object) -> list[dict[str, str]]:
    """Bind all SDK items' identities/quantities, not their changing valuations.

    Only the verified items/symbol/quantity shape is supported. Do not filter
    unmanaged holdings, infer missing quantities, merge duplicate symbols, or
    round quantities to an assumed instrument precision.
    """

    payload = _json_value(payload, surface="positions")
    try:
        if not isinstance(payload, Mapping) or payload.get("error"):
            raise ValueError
        rows = payload.get("items")
        if not isinstance(rows, list):
            raise ValueError
        quantities = []
        symbols = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError
            symbol, value = row.get("symbol"), row.get("quantity")
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError
            symbol = symbol.strip().upper()
            if symbol in symbols:
                raise ValueError
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError
            quantity = Decimal(str(value))
            if not quantity.is_finite():
                raise ValueError
            # Decimal.normalize() can round under the current decimal context.
            text = format(quantity, "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            quantities.append({"symbol": symbol, "quantity": "0" if quantity == 0 else text})
            symbols.add(symbol)
        return sorted(quantities, key=lambda row: row["symbol"])
    except (InvalidOperation, ValueError) as exc:
        raise FirstradeReconciliationUnavailable(
            "Firstrade reconciliation received incomplete position facts."
        ) from None


@dataclass(frozen=True)
class FirstradeReconciliationObservations:
    """Sensitive in-memory observations. Never serialize this object."""

    account_scope: Mapping[str, object]
    account_identity_match: bool
    positions: object
    cash: Mapping[str, object]
    open_orders: tuple[Mapping[str, object], ...]
    recent_executions: Mapping[str, object]
    recent_executions_available: bool


@dataclass(frozen=True)
class FirstradeReconciliationCandidate:
    """Public-safe candidate containing only hashes, booleans, and reason codes."""

    evidence: BrokerReconciliationEvidence
    recovery_blockers: tuple[BrokerReconciliationFinding, ...]
    expected_digests_configured: bool
    execution_ledger_records_count: int
    recent_executions_available: bool
    local_execution_ledger_available: bool

    @property
    def permits_active_lkg(self) -> bool:
        return not self.recovery_blockers

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "schema_version": "firstrade_reconciliation_candidate.v1",
            "permits_active_lkg": self.permits_active_lkg,
            "expected_digests_configured": self.expected_digests_configured,
            "execution_ledger_records_count": self.execution_ledger_records_count,
            "recent_executions_available": self.recent_executions_available,
            "local_execution_ledger_available": self.local_execution_ledger_available,
            "recovery_blockers": [finding.value for finding in self.recovery_blockers],
            "evidence": self.evidence.to_dict(),
        }


def validate_reconciliation_preconditions(
    *,
    runtime_target: object,
    client_builder: object,
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
) -> None:
    """Reject before client or runtime context construction whenever unsafe."""

    if not reconciliation_enabled(env_reader):
        raise FirstradeReconciliationUnavailable("Firstrade broker reconciliation is disabled.")
    if not callable(client_builder):
        raise FirstradeReconciliationUnavailable("Firstrade broker reconciliation client is unavailable.")
    if runtime_target is None:
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation requires an explicit runtime target.")
    continuity = getattr(runtime_target, "live_continuity", None)
    if _text(getattr(continuity, "state", "")).upper() != "RECONCILE_ONLY":
        raise FirstradeReconciliationUnavailable(
            "Firstrade reconciliation is only available for a frozen baseline."
        )


def collect_read_only_reconciliation_observations(
    client: Any,
    *,
    requested_account: object,
) -> FirstradeReconciliationObservations:
    """Call only verified account, balances, positions, and orders readers."""

    account_numbers = getattr(client, "account_numbers", None)
    select_account = getattr(client, "select_account", None)
    get_balances = getattr(client, "get_balances", None)
    get_positions = getattr(client, "get_positions", None)
    get_orders = getattr(client, "get_orders", None)
    if not all(callable(method) for method in (account_numbers, select_account, get_balances, get_positions, get_orders)):
        raise FirstradeReconciliationUnavailable(
            "Firstrade reconciliation requires read-only account, balance, position, and order APIs."
        )
    account = _text(requested_account)
    if not account:
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation requires an explicit account.")
    known_accounts = {_text(value) for value in account_numbers()} - {""}
    selected_account = _text(select_account(account))
    if account not in known_accounts or selected_account != account:
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation account identity is incomplete.")
    balances = get_balances(account)
    positions = get_positions(account)
    try:
        orders = get_orders(account, per_page=0)
    except TypeError as exc:
        raise FirstradeReconciliationUnavailable(
            "Firstrade reconciliation requires the bounded read-only order API."
        ) from exc
    if not isinstance(balances, Mapping) or not balances:
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation received incomplete balances.")
    if not isinstance(positions, Mapping):
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation received incomplete positions.")
    _position_quantities(positions)
    if not isinstance(orders, list) or any(not isinstance(order, Mapping) for order in orders):
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation received incomplete orders.")
    open_orders: list[Mapping[str, object]] = []
    for order in orders:
        status = _text(order.get("status") or order.get("order_status") or order.get("state")).upper()
        if not status:
            raise FirstradeReconciliationUnavailable("Firstrade reconciliation received an order without status.")
        if status not in _TERMINAL_ORDER_STATUSES:
            open_orders.append(order)
    # No verified bounded timestamps or fill semantics: never label order rows executions.
    return FirstradeReconciliationObservations(
        account_scope={"account_id": account},
        account_identity_match=True,
        positions=_json_value(positions, surface="positions"),
        cash=dict(_json_value(balances, surface="balances")),
        open_orders=_canonical_records(open_orders),
        recent_executions={"availability": "unavailable"},
        recent_executions_available=False,
    )


def _expected_digests(
    *, env_reader: Callable[[str, str | None], str | None] = os.getenv
) -> Mapping[str, str] | None:
    raw = _text(env_reader(FIRSTRADE_RECONCILIATION_EXPECTED_DIGESTS_ENV, None))
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation expected digests are invalid.") from exc
    if not isinstance(value, Mapping) or set(value) != set(_EXPECTED_DIGEST_KEYS):
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation expected digests are incomplete.")
    normalized = {key: _text(value[key]).lower().removeprefix("sha256:") for key in _EXPECTED_DIGEST_KEYS}
    if any(len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest) for digest in normalized.values()):
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation expected digests are invalid.")
    return normalized


def _continuity_fields(runtime_target: object) -> tuple[str, str, str]:
    continuity = getattr(runtime_target, "live_continuity", None)
    baseline_id = _text(getattr(continuity, "baseline_id", ""))
    baseline_target_sha256 = _text(getattr(continuity, "baseline_target_sha256", "")).lower()
    if not baseline_id or len(baseline_target_sha256) != 64:
        raise FirstradeReconciliationUnavailable(
            "Firstrade reconciliation requires a frozen live-continuity baseline."
        )
    return baseline_id, baseline_target_sha256, baseline_target_sha256


def build_reconciliation_candidate(
    *,
    observations: FirstradeReconciliationObservations,
    runtime_target: object,
    project_id: str | None,
    ledger_digest_reader: Callable[[], tuple[str, int]],
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
    observed_at: datetime | None = None,
) -> FirstradeReconciliationCandidate:
    """Build redacted evidence; incomplete evidence can never permit recovery."""

    del project_id  # The injected reader owns its read-only storage configuration.
    expected = _expected_digests(env_reader=env_reader)
    platform_id = _text(getattr(runtime_target, "platform_id", ""))
    strategy_profile = _text(getattr(runtime_target, "strategy_profile", ""))
    account_scope = _text(getattr(runtime_target, "account_scope", ""))
    if platform_id != "firstrade" or not strategy_profile or not account_scope:
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation runtime target is incomplete.")
    baseline_id, baseline_target_sha256, runtime_target_sha256 = _continuity_fields(runtime_target)
    digests = {
        # Existing expected raw-payload digests intentionally do not migrate here.
        "positions_sha256": calculate_broker_observation_sha256(_position_quantities(observations.positions)),
        "cash_sha256": calculate_broker_observation_sha256(observations.cash),
        "open_orders_sha256": calculate_broker_observation_sha256(observations.open_orders),
        "recent_executions_sha256": calculate_broker_observation_sha256(observations.recent_executions),
    }
    try:
        ledger_digest, records_count = ledger_digest_reader()
        local_execution_ledger_available = isinstance(records_count, int) and records_count >= 0
        digests["local_execution_ledger_sha256"] = str(ledger_digest).lower().removeprefix("sha256:")
        if len(digests["local_execution_ledger_sha256"]) != 64:
            local_execution_ledger_available = False
    except Exception:
        local_execution_ledger_available = False
        records_count = 0
        digests["local_execution_ledger_sha256"] = calculate_broker_observation_sha256(
            {"availability": "unavailable"}
        )
    timestamp = observed_at or datetime.now(timezone.utc)
    evidence = build_broker_reconciliation_evidence(
        platform_id=platform_id,
        strategy_profile=strategy_profile,
        account_scope_sha256=calculate_broker_observation_sha256(observations.account_scope),
        baseline_id=baseline_id,
        baseline_target_sha256=baseline_target_sha256,
        runtime_target_sha256=runtime_target_sha256,
        observed_at=timestamp,
        broker_connected=True,
        account_identity_match=observations.account_identity_match,
        positions_match=expected is not None and expected["positions_sha256"] == digests["positions_sha256"],
        cash_match=expected is not None and expected["cash_sha256"] == digests["cash_sha256"],
        open_orders_match=expected is not None and expected["open_orders_sha256"] == digests["open_orders_sha256"],
        recent_executions_match=(
            observations.recent_executions_available
            and expected is not None
            and expected["recent_executions_sha256"] == digests["recent_executions_sha256"]
        ),
        local_execution_ledger_match=(
            local_execution_ledger_available
            and expected is not None
            and expected["local_execution_ledger_sha256"] == digests["local_execution_ledger_sha256"]
        ),
        **digests,
    )
    blockers = evaluate_broker_reconciliation_recovery(
        evidence,
        now=timestamp,
        expected_platform_id=platform_id,
        expected_strategy_profile=strategy_profile,
        expected_account_scope_sha256=(expected or {}).get("account_scope_sha256"),
        expected_baseline_id=baseline_id,
        expected_runtime_target_sha256=runtime_target_sha256,
        **{
            f"expected_{key}": (expected or {}).get(key)
            for key in _EXPECTED_DIGEST_KEYS
            if key != "account_scope_sha256"
        },
    )
    return FirstradeReconciliationCandidate(
        evidence=evidence,
        recovery_blockers=blockers,
        expected_digests_configured=expected is not None,
        execution_ledger_records_count=records_count if local_execution_ledger_available else 0,
        recent_executions_available=observations.recent_executions_available,
        local_execution_ledger_available=local_execution_ledger_available,
    )


def validate_reconciliation_candidate(candidate: object) -> dict[str, object]:
    """Reject malformed receipts rather than returning sensitive observations."""

    try:
        payload = candidate.to_safe_dict()
        evidence = BrokerReconciliationEvidence.from_dict(payload["evidence"])
    except Exception as exc:
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation receipt is invalid.") from exc
    if set(payload) != _SAFE_CANDIDATE_KEYS:
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation receipt is invalid.")
    if payload.get("schema_version") != "firstrade_reconciliation_candidate.v1" or evidence.platform_id != "firstrade":
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation receipt is invalid.")
    normalized = dict(payload)
    normalized["evidence"] = evidence.to_dict()
    return normalized


def collect_broker_reconciliation_evidence(
    *,
    collector: Callable[[], BrokerReconciliationEvidence] | None = None,
) -> BrokerReconciliationEvidence:
    """Compatibility injection boundary; never construct broker or order context."""

    if not callable(collector):
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation collector is not configured.")
    evidence = collector()
    if not isinstance(evidence, BrokerReconciliationEvidence):
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation collector must return QPK evidence.")
    if evidence.platform_id != "firstrade":
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation evidence has the wrong platform.")
    return evidence
