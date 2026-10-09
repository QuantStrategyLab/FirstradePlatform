"""Small cached-session-only Firstrade account facts collector.

The caller owns client construction and protected target/account selection. This
module only accepts a client already connected through ``connect_read_only``;
it never authenticates, persists, notifies, or submits orders.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Callable

from application.account_payload_utils import flatten_values, get_first

_DECIMAL_RE = re.compile(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", re.ASCII)
_ISO_CURRENCY_RE = re.compile(r"[A-Z]{3}", re.ASCII)
_MAX_DECIMAL_TEXT_LENGTH = 64
_MAX_DECIMAL_INTEGER_DIGITS = 30
_MAX_DECIMAL_SCALE = 28


class AccountFactsUnavailable(RuntimeError):
    """A fixed safe failure for account facts that cannot be trusted."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AccountFactsUnavailable("observation_time_invalid")
    return value.astimezone(timezone.utc).isoformat()


def _decimal_text(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise AccountFactsUnavailable("provider_value_invalid")
    text = str(value).strip()
    if not text:
        return None
    if len(text) > _MAX_DECIMAL_TEXT_LENGTH:
        raise AccountFactsUnavailable("provider_value_out_of_range")
    negative_parentheses = text.startswith("(") and text.endswith(")")
    if negative_parentheses:
        text = text[1:-1].strip()
    if text.startswith("$"):
        text = text[1:].strip()
    if not _DECIMAL_RE.fullmatch(text) or (negative_parentheses and text.startswith(("+", "-"))):
        raise AccountFactsUnavailable("provider_value_invalid")
    unsigned = text.lstrip("+-").replace(",", "")
    whole, _, fraction = unsigned.partition(".")
    if len(whole) > _MAX_DECIMAL_INTEGER_DIGITS or len(fraction) > _MAX_DECIMAL_SCALE:
        raise AccountFactsUnavailable("provider_value_out_of_range")
    try:
        number = Decimal(text.replace(",", ""))
    except InvalidOperation:
        raise AccountFactsUnavailable("provider_value_invalid") from None
    if not number.is_finite():
        raise AccountFactsUnavailable("provider_value_invalid")
    if negative_parentheses:
        number = -abs(number)
    if number == 0:
        return "0"
    normalized = format(number, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


def _exact_balance_value(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    # The provider response is not a formally versioned schema. Only consume
    # the exact account-level labels we have observed; nested objects may
    # describe margin, positions, or another balance scope.
    matches = [parsed for key in keys if (parsed := _decimal_text(payload.get(key))) is not None]
    if len(set(matches)) > 1:
        raise AccountFactsUnavailable("balances_ambiguous")
    return matches[0] if matches else None


def _currency_code(payload: Mapping[str, Any]) -> str | None:
    currencies = []
    for key in ("currency", "currency_code"):
        value = payload.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise AccountFactsUnavailable("provider_currency_invalid")
        code = value.strip().upper()
        if not _ISO_CURRENCY_RE.fullmatch(code):
            raise AccountFactsUnavailable("provider_currency_invalid")
        currencies.append(code)
    if len(set(currencies)) > 1:
        raise AccountFactsUnavailable("provider_currency_ambiguous")
    return currencies[0] if currencies else None


def _position_rows(payload: object) -> list[Mapping[str, Any]]:
    rows: object
    if isinstance(payload, Mapping):
        for key in ("items", "positions", "data", "result"):
            if key in payload:
                rows = payload[key]
                break
        else:
            if "symbol" not in payload:
                raise AccountFactsUnavailable("positions_unavailable")
            rows = [payload]
    else:
        rows = payload
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise AccountFactsUnavailable("positions_unavailable")
    return list(rows)


def _position_fact(row: Mapping[str, Any]) -> tuple[dict[str, str | None], bool, bool, bool]:
    raw_symbol = get_first(row, "symbol", "ticker", "security_symbol")
    symbol = str(raw_symbol).strip().upper() if raw_symbol is not None else ""
    quantity = _decimal_text(get_first(row, "quantity", "shares", "qty"))
    market_value = _decimal_text(get_first(row, "market_value", "marketValue", "value", "current_value"))
    currency = _currency_code(row)
    return (
        {
            "symbol": symbol or None,
            "quantity": quantity,
            "market_value": market_value,
            "currency": currency,
        },
        not symbol or quantity is None,
        market_value is None,
        currency is None,
    )


def collect_firstrade_account_facts(
    client: object,
    *,
    expected_account: str,
    include_positions: bool = True,
    clock: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Read one exact provider account through an already cached read-only client.

    ``expected_account`` must come from the caller's approved target binding.
    The native identifier is included only for the trusted account-facts
    publisher. Callers must never expose this object in an HTTP response or log.
    """

    expected = expected_account.strip() if isinstance(expected_account, str) else ""
    if not expected or expected != expected_account:
        raise AccountFactsUnavailable("account_identity_unavailable")
    if (
        getattr(client, "session_reused", None) is not True
        or getattr(client, "read_only_transport_enabled", None) is not True
        or getattr(client, "live_trading_enabled", None) is not False
        or getattr(client, "session", None) is None
        or getattr(client, "account_data", None) is None
        or not callable(getattr(client, "account_numbers", None))
        or not callable(getattr(client, "select_account", None))
        or not callable(getattr(client, "get_balances", None))
        or (include_positions and not callable(getattr(client, "get_positions", None)))
    ):
        raise AccountFactsUnavailable("readonly_client_unavailable")

    try:
        started_at = _timestamp(clock())
    except AccountFactsUnavailable:
        raise
    except Exception:
        raise AccountFactsUnavailable("observation_time_invalid") from None
    try:
        accounts = client.account_numbers()
        if not isinstance(accounts, list) or any(
            not isinstance(account, str) or not account for account in accounts
        ):
            raise AccountFactsUnavailable("account_identity_mismatch")
        if not accounts:
            raise AccountFactsUnavailable("account_list_empty")
        if accounts.count(expected) != 1:
            raise AccountFactsUnavailable("account_identity_mismatch")
        selected_account = client.select_account(expected)
        if selected_account != expected:
            raise AccountFactsUnavailable("account_identity_mismatch")
    except AccountFactsUnavailable:
        raise
    except Exception:
        raise AccountFactsUnavailable("account_identity_unavailable") from None

    try:
        balances = client.get_balances(expected)
    except Exception:
        raise AccountFactsUnavailable("balances_unavailable") from None
    if not isinstance(balances, Mapping) or not balances or balances.get("error"):
        raise AccountFactsUnavailable("balances_unavailable")

    positions_payload = None
    if include_positions:
        try:
            positions_payload = client.get_positions(expected)
        except Exception:
            raise AccountFactsUnavailable("positions_unavailable") from None
    try:
        finished_at = _timestamp(clock())
    except AccountFactsUnavailable:
        raise
    except Exception:
        raise AccountFactsUnavailable("observation_time_invalid") from None
    try:
        if datetime.fromisoformat(finished_at) < datetime.fromisoformat(started_at):
            raise AccountFactsUnavailable("observation_time_invalid")
        rows = _position_rows(positions_payload) if include_positions else []
    except AccountFactsUnavailable:
        raise

    positions = [] if include_positions else None
    missing_identity = 0
    missing_market_value = 0
    missing_currency = 0
    for row in rows:
        fact, identity_missing, value_missing, currency_missing = _position_fact(row)
        assert positions is not None
        positions.append(fact)
        missing_identity += int(identity_missing)
        missing_market_value += int(value_missing)
        missing_currency += int(currency_missing)

    provider_equity = _exact_balance_value(balances, ("total_equity",))
    cash_balance = _exact_balance_value(balances, ("cash_balance",))
    available_cash = _exact_balance_value(balances, ("available_cash",))
    buying_power = _exact_balance_value(balances, ("buying_power",))
    balance_currency = _currency_code(balances)

    return {
        "status": "available",
        "platform": "firstrade",
        "account_selector_status": "matched",
        "broker_account_id": expected,
        "observed_started_at": started_at,
        "observed_finished_at": finished_at,
        "balances": {
            "provider_equity": provider_equity,
            "cash_balance": cash_balance,
            "available_cash": available_cash,
            "buying_power": buying_power,
            "currency": balance_currency,
        },
        "positions_status": "available" if include_positions else "not_requested",
        "positions": positions,
        "coverage": {
            "positions_requested": include_positions,
            "positions_count": len(positions) if positions is not None else None,
            "positions_missing_market_value": missing_market_value if include_positions else None,
            "positions_missing_identity_fields": missing_identity if include_positions else None,
            "provider_equity_available": provider_equity is not None,
            "cash_balance_available": cash_balance is not None,
            "available_cash_available": available_cash is not None,
            "buying_power_available": buying_power is not None,
            "balance_currency_available": balance_currency is not None,
            "positions_missing_currency": missing_currency if include_positions else None,
            "currency_coverage_complete": include_positions and balance_currency is not None and missing_currency == 0,
        },
    }
