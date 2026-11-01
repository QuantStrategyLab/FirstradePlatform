"""Fee schedule configuration for Firstrade statement reconciliation.

All rates are data, not logic. Each table is a list of segments keyed by the
first trade date on which the segment applies; a lookup picks the latest
segment whose ``effective_from`` is on or before the trade date. Dates before
the first segment, and dates after ``verified_through`` (the last day the
rates below were checked against the published sources), are unsupported and
fail closed (``FeeScheduleError``) so a reconciliation never silently applies
a rate that was not verified. In particular the SEC fiscal year 2027 Section 31
rate has not been published yet; the FY2026 rate stays in effect until 60 days
after the FY2027 appropriation is enacted, so no date past ``verified_through``
is assumed to keep the FY2026 rate.

Sources (checked 2026-11-01):
- Broker-assisted order fee: https://www.firstrade.com/trading/pricing
- SEC Section 31 rate advisories (charge date is the trade date):
  FY2024 $27.80/M from 2024-05-22, FY2025 $0.00/M from 2025-05-14,
  FY2026 $20.60/M from 2026-04-04.
- FINRA Trading Activity Fee (TAF), covered equity securities:
  SR-FINRA-2024-019 (2025 fee adjustment schedule: $0.000166/share, max $8.30
  until 2025-12-31; $0.000195/share, max $9.79 from 2026-01-01),
  SR-FINRA-2026-021 (rate $0 for 2026-10-01..2026-12-31, Release 34-106409) and
  SR-FINRA-2026-020 (Release 34-106410, filed 2026-09-15, immediately
  effective) which defers the remaining increases by two years: 2027 and 2028
  stay at $0.000195/share (max $9.79), 2029 is $0.000232/share (max $11.61),
  2030 is $0.000240 (max $12.05) and 2031 is $0.000249 (max $12.50).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


class FeeScheduleError(ValueError):
    """The fee schedule is malformed or does not cover the requested date."""


DEFAULT_FEE_SCHEDULE_CONFIG: dict[str, Any] = {
    "schema_version": "firstrade_fee_schedule.v1",
    # Last trade date for which every rate below was verified against the
    # published sources. Later trade dates are unsupported (fail closed).
    "verified_through": "2026-12-01",
    # Matched case-insensitively against the transaction description.
    "broker_assisted_markers": ["BROKER/DEALER ORDER"],
    "broker_assisted_fee": [
        {"effective_from": "2000-01-01", "amount": "19.95"},
    ],
    # Dollars per $1,000,000 of covered sale proceeds.
    "sec_rate_per_million": [
        {"effective_from": "2024-05-22", "rate": "27.80"},
        {"effective_from": "2025-05-14", "rate": "0.00"},
        {"effective_from": "2026-04-04", "rate": "20.60"},
    ],
    # FINRA TAF for covered equity securities (stocks and ETFs).
    "taf_per_share": [
        {"effective_from": "2024-01-01", "rate": "0.000166", "max_per_trade": "8.30"},
        {"effective_from": "2026-01-01", "rate": "0.000195", "max_per_trade": "9.79"},
        {"effective_from": "2026-10-01", "rate": "0", "max_per_trade": "0"},
        # SR-FINRA-2026-021 pause ends; SR-FINRA-2026-020 keeps 2026 rates.
        {"effective_from": "2027-01-01", "rate": "0.000195", "max_per_trade": "9.79"},
        {"effective_from": "2029-01-01", "rate": "0.000232", "max_per_trade": "11.61"},
        {"effective_from": "2030-01-01", "rate": "0.000240", "max_per_trade": "12.05"},
        {"effective_from": "2031-01-01", "rate": "0.000249", "max_per_trade": "12.50"},
    ],
    "minimum_regulatory_fee": "0.01",
    "explained_residual_tolerance": "0.01",
}


@dataclass(frozen=True)
class FeeSchedule:
    broker_assisted_markers: tuple[str, ...]
    broker_assisted_fee: tuple[tuple[date, Decimal], ...]
    sec_rate_per_million: tuple[tuple[date, Decimal], ...]
    taf_per_share: tuple[tuple[date, Decimal, Decimal], ...]
    minimum_regulatory_fee: Decimal
    explained_residual_tolerance: Decimal
    verified_through: date

    def _lookup(self, table: tuple[tuple[Any, ...], ...], trade_date: date, name: str):
        if trade_date > self.verified_through:
            raise FeeScheduleError(
                f"{name}_not_verified_after_{self.verified_through.isoformat()}")
        selected = None
        for row in table:
            if row[0] <= trade_date:
                selected = row
        if selected is None:
            raise FeeScheduleError(f"{name}_not_defined_for_{trade_date.isoformat()}")
        return selected

    def broker_assisted_fee_on(self, trade_date: date) -> Decimal:
        return self._lookup(self.broker_assisted_fee, trade_date, "broker_assisted_fee")[1]

    def sec_rate_on(self, trade_date: date) -> Decimal:
        return self._lookup(self.sec_rate_per_million, trade_date, "sec_rate")[1]

    def taf_on(self, trade_date: date) -> tuple[Decimal, Decimal]:
        row = self._lookup(self.taf_per_share, trade_date, "taf_rate")
        return row[1], row[2]

    def is_broker_assisted(self, description: object) -> bool:
        text = str(description or "").upper()
        return any(marker.upper() in text for marker in self.broker_assisted_markers)


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise FeeScheduleError(f"{field}_invalid")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise FeeScheduleError(f"{field}_invalid") from None
    if not number.is_finite() or number < 0:
        raise FeeScheduleError(f"{field}_invalid")
    return number


def _date(value: object, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise FeeScheduleError(f"{field}_invalid") from None


def _segments(raw: object, field: str, value_keys: tuple[str, ...]) -> tuple[tuple[Any, ...], ...]:
    if not isinstance(raw, list) or not raw:
        raise FeeScheduleError(f"{field}_missing")
    rows = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise FeeScheduleError(f"{field}_invalid")
        rows.append((_date(item.get("effective_from"), field),
                     *(_decimal(item.get(key), f"{field}.{key}") for key in value_keys)))
    dates = [row[0] for row in rows]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise FeeScheduleError(f"{field}_not_strictly_ordered")
    return tuple(rows)


def load_fee_schedule(config: Mapping[str, Any] | None = None) -> FeeSchedule:
    """Validate a schedule mapping (default: the packaged one)."""

    data = DEFAULT_FEE_SCHEDULE_CONFIG if config is None else config
    if data.get("schema_version") != "firstrade_fee_schedule.v1":
        raise FeeScheduleError("schema_version_unsupported")
    markers = data.get("broker_assisted_markers")
    if (not isinstance(markers, list) or not markers
            or any(not isinstance(m, str) or not m.strip() for m in markers)):
        raise FeeScheduleError("broker_assisted_markers_invalid")
    if data.get("verified_through") in (None, ""):
        raise FeeScheduleError("verified_through_missing")
    return FeeSchedule(
        broker_assisted_markers=tuple(markers),
        broker_assisted_fee=_segments(data.get("broker_assisted_fee"), "broker_assisted_fee", ("amount",)),
        sec_rate_per_million=_segments(data.get("sec_rate_per_million"), "sec_rate", ("rate",)),
        taf_per_share=_segments(data.get("taf_per_share"), "taf_rate", ("rate", "max_per_trade")),
        minimum_regulatory_fee=_decimal(data.get("minimum_regulatory_fee"), "minimum_regulatory_fee"),
        explained_residual_tolerance=_decimal(
            data.get("explained_residual_tolerance"), "explained_residual_tolerance"),
        verified_through=_date(data.get("verified_through"), "verified_through"),
    )
