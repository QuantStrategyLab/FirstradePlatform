"""Pure, offline helpers that explain Firstrade statement reconciliation gaps.

Nothing here performs I/O, reads credentials or calls a broker. Inputs are
statement/CSV rows and the read-only account facts already collected.

Two gap classes are modelled explicitly instead of being left "unexplained":

* ``decompose_sell_fees`` - a desk-executed (``BROKER/DEALER ORDER``, which
  includes ``MARGIN SELLOUT``) sell nets less than quantity x price because of
  the broker-assisted order fee and the regulatory pass-through fee.
* ``valuation_basis_difference`` - the balances endpoint and the positions
  endpoint value the same shares from different quote bases, so
  ``balance long_stock_value`` and ``positions market_value`` can differ by a
  few cents without any cash or quantity discrepancy. The tolerance has an
  absolute cap and the share counts are cross-checked when available.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from application.firstrade_fee_schedule import FeeSchedule, FeeScheduleError, load_fee_schedule

_CENT = Decimal("0.01")
_MILLION = Decimal("1000000")

VALUATION_TOLERANCE_FLOOR = Decimal("0.10")
VALUATION_TOLERANCE_RATE = Decimal("0.001")
# Absolute ceiling so 0.1% of a large account can never hide a real break.
VALUATION_TOLERANCE_CAP = Decimal("25.00")

_EXEC_TIME_RE = re.compile(r"EXEC TIME:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_SETTLEMENT_RE = re.compile(r"S/D:\s*(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("amount_invalid")
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        raise ValueError("amount_invalid") from None
    if not number.is_finite():
        raise ValueError("amount_invalid")
    return number


def _trade_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("trade_date_invalid")


def _resolve_dates(csv_date: object, description: object) -> tuple[date, date | None]:
    """Return ``(trade_date, settlement_date)`` for a history row.

    In the Firstrade history CSV the ``Date`` column is the trade date: for the
    2026-08-28 IBIT sell it is ``8/28/2026`` while the description carries
    ``EXEC TIME: 2026-08-28 ...`` and ``S/D: 08/31/2026`` (T+1 settlement).
    SEC Section 31 and FINRA TAF are charged on the trade date, so the
    settlement date must never select a rate. The row fails closed when the
    ``Date`` column disagrees with ``EXEC TIME`` or equals/precedes ``S/D``,
    which would mean an export that carries settlement dates.
    """

    trade_date = _trade_date(csv_date)
    text = str(description or "")
    executed = _EXEC_TIME_RE.search(text)
    if executed and _trade_date(executed.group(1)) != trade_date:
        raise ValueError("date_column_not_trade_date")
    settled = _SETTLEMENT_RE.search(text)
    settlement_date = _trade_date(settled.group(1)) if settled else None
    if settlement_date is not None and settlement_date <= trade_date:
        raise ValueError("date_column_not_trade_date")
    return trade_date, settlement_date


def _row_value(row: Mapping[str, Any], *keys: str) -> Any:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def _round_up_cent(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_CEILING)


def _money(value: Decimal) -> str:
    return format(value.quantize(_CENT, rounding=ROUND_HALF_UP), "f")


def _exact(value: Decimal) -> str:
    return format(value.normalize(), "f") if value != 0 else "0"


def regulatory_fee(
    *, proceeds: Decimal, quantity: Decimal, trade_date: date, schedule: FeeSchedule
) -> dict[str, Decimal]:
    """SEC Section 31 plus FINRA TAF on a sale, collapsed to one cent-rounded fee.

    Firstrade shows a single ``FEES`` line, so the unrounded SEC and TAF
    amounts are summed, rounded up to the next cent, and floored at the
    configured minimum (``$0.01``) whenever any fee is owed. A rate of zero
    (e.g. the SEC rate between 2025-05-14 and 2026-04-03) yields no fee.
    """

    sec_raw = proceeds * schedule.sec_rate_on(trade_date) / _MILLION
    taf_rate, taf_max = schedule.taf_on(trade_date)
    taf_raw = min(abs(quantity) * taf_rate, taf_max) if taf_rate > 0 else Decimal(0)
    total_raw = sec_raw + taf_raw
    if total_raw <= 0:
        fee = Decimal("0.00")
    else:
        fee = max(_round_up_cent(total_raw), schedule.minimum_regulatory_fee)
    return {"sec_raw": sec_raw, "taf_raw": taf_raw, "fee": fee}


def decompose_sell_fees(
    row: Mapping[str, Any], *, schedule: FeeSchedule | None = None
) -> dict[str, Any]:
    """Explain the gap between ``quantity x price`` and the reported net proceeds.

    ``row`` uses the Firstrade history CSV shape: Date (the trade date, not the
    settlement date), Transaction, Quantity, Description, Symbol, Price, Amount
    (case-insensitive keys). Quantity and price must be positive. Returns a plain
    dict with fixed keys; ``status`` is one of ``explained``, ``unexplained``,
    ``not_applicable`` or ``unsupported``. The schedule is applied by trade
    date and fails closed (``unsupported``) when a rate is not defined.
    """

    schedule = schedule or load_fee_schedule()
    transaction = str(_row_value(row, "transaction") or "").strip().lower()
    description = _row_value(row, "description")
    base = {"status": "not_applicable", "reason": None}
    if transaction not in {"sold", "sell"}:
        return {**base, "reason": "not_a_sell"}
    if not schedule.is_broker_assisted(description):
        return {**base, "reason": "not_broker_assisted_order"}
    try:
        trade_date, settlement_date = _resolve_dates(
            _row_value(row, "date", "trade_date"), description)
        quantity = abs(_to_decimal(_row_value(row, "quantity")))
        price = _to_decimal(_row_value(row, "price"))
        reported_net = _to_decimal(_row_value(row, "amount", "net_amount"))
        if quantity <= 0:
            raise ValueError("quantity_not_positive")
        if price <= 0:
            raise ValueError("price_not_positive")
    except ValueError as exc:
        return {"status": "unsupported", "reason": str(exc)}
    gross = quantity * price
    try:
        broker_fee = schedule.broker_assisted_fee_on(trade_date)
        reg = regulatory_fee(
            proceeds=gross, quantity=quantity, trade_date=trade_date, schedule=schedule)
    except FeeScheduleError as exc:
        return {"status": "unsupported", "reason": str(exc)}
    expected_net = (gross - broker_fee - reg["fee"]).quantize(_CENT, rounding=ROUND_HALF_UP)
    residual = (reported_net - expected_net).quantize(_CENT)
    explained = abs(residual) <= schedule.explained_residual_tolerance
    return {
        "status": "explained" if explained else "unexplained",
        "reason": None if explained else "residual_exceeds_tolerance",
        "trade_date": trade_date.isoformat(),
        "settlement_date": settlement_date.isoformat() if settlement_date else None,
        "gross_proceeds": _exact(gross),
        "gross_minus_reported_net": _exact(gross - reported_net),
        "broker_assisted_fee": _money(broker_fee),
        "regulatory_fee": _money(reg["fee"]),
        "sec_fee_unrounded": _exact(reg["sec_raw"]),
        "taf_unrounded": _exact(reg["taf_raw"]),
        "expected_net": _money(expected_net),
        "reported_net": _money(reported_net),
        "residual": _money(residual),
        "residual_tolerance": _money(schedule.explained_residual_tolerance),
    }


def _quantity_map(raw: Mapping[str, object] | None) -> dict[str, Decimal] | None:
    if raw is None:
        return None
    result: dict[str, Decimal] = {}
    for symbol, quantity in raw.items():
        key = str(symbol or "").strip().upper()
        if not key or key in result:
            raise ValueError("quantity_invalid")
        value = _to_decimal(quantity)
        if value <= 0:
            raise ValueError("quantity_invalid")
        result[key] = value
    return result


def valuation_basis_difference(
    *,
    balance_long_stock_value: object,
    positions_market_values: Iterable[object],
    position_quantities: Mapping[str, object] | None = None,
    expected_quantities: Mapping[str, object] | None = None,
    tolerance_cap: Decimal = VALUATION_TOLERANCE_CAP,
) -> dict[str, str]:
    """Compare balances-endpoint stock value with the sum of positions market values.

    A non-zero gap within ``min(max($0.10, 0.1% of market value), cap)`` (cap
    ``$25.00``) is reported as ``valuation_basis_difference`` (two endpoints,
    two quote bases) and is not an unexplained reconciliation break. A gap
    above the tolerance is ``unexplained``. The gap is signed
    ``positions - balance``.

    A price-basis gap is only credible when the share counts agree, so the
    position quantities are cross-checked against ``expected_quantities`` (for
    example the previous snapshot or the ledger) when both are supplied: any
    difference makes the result ``unexplained`` (``quantity_mismatch``).
    ``quantity_check`` is ``not_performed`` when either side is missing, so a
    caller can see the quantities were not verified. An empty positions list
    only matches a zero balance value.
    """

    try:
        balance_value = _to_decimal(balance_long_stock_value)
        values = [_to_decimal(v) for v in positions_market_values]
        actual = _quantity_map(position_quantities)
        expected = _quantity_map(expected_quantities)
    except ValueError:
        return {"status": "unavailable", "reason": "valuation_input_missing"}
    if balance_value < 0 or any(v < 0 for v in values):
        return {"status": "unavailable", "reason": "valuation_input_negative"}
    positions_value = sum(values, Decimal(0))
    difference = positions_value - balance_value
    reference = max(abs(balance_value), abs(positions_value))
    tolerance = min(
        max(VALUATION_TOLERANCE_FLOOR, reference * VALUATION_TOLERANCE_RATE), tolerance_cap)
    quantity_check = "not_performed"
    if actual is not None and expected is not None:
        quantity_check = "matched" if actual == expected else "mismatch"
    reason = None
    if not values and balance_value != 0:
        status, reason = "unexplained", "positions_empty_balance_nonzero"
    elif quantity_check == "mismatch":
        status, reason = "unexplained", "quantity_mismatch"
    elif difference == 0:
        status = "match"
    elif abs(difference) <= tolerance:
        status = "valuation_basis_difference"
    else:
        status, reason = "unexplained", "difference_exceeds_tolerance"
    report = {
        "status": status,
        "balance_long_stock_value": _exact(balance_value),
        "positions_market_value": _exact(positions_value),
        "difference_positions_minus_balance": _exact(difference),
        "tolerance": _exact(tolerance),
        "quantity_check": quantity_check,
    }
    if reason:
        report["reason"] = reason
    return report


def valuation_basis_report(
    observation: Mapping[str, Any], *, expected_quantities: Mapping[str, object] | None = None
) -> dict[str, str]:
    """Build the report from a ``collect_firstrade_account_facts`` observation."""

    balances = observation.get("balances")
    positions = observation.get("positions")
    if not isinstance(balances, Mapping) or not isinstance(positions, list):
        return {"status": "unavailable", "reason": "observation_incomplete"}
    if balances.get("long_stock_value") is None:
        return {"status": "unavailable", "reason": "balance_long_stock_value_missing"}
    rows = [row for row in positions if isinstance(row, Mapping)]
    values = [row.get("market_value") for row in rows]
    if any(v is None for v in values):
        return {"status": "unavailable", "reason": "position_market_value_missing"}
    quantities: dict[str, object] | None = None
    if expected_quantities is not None:
        quantities = {}
        for row in rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol or symbol in quantities or row.get("quantity") is None:
                return {"status": "unavailable", "reason": "position_quantity_missing"}
            quantities[symbol] = row["quantity"]
    return valuation_basis_difference(
        balance_long_stock_value=balances["long_stock_value"],
        positions_market_values=values,
        position_quantities=quantities,
        expected_quantities=expected_quantities,
    )
