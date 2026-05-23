"""Helpers for normalizing Firstrade account payload shapes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def flatten_values(payload: Any, prefix: str = "") -> dict[str, Any]:
    values: dict[str, Any] = {}
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            values.update(flatten_values(value, child_key))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            values.update(flatten_values(value, f"{prefix}.{index}"))
    else:
        values[prefix] = payload
    return values


def first_numeric_by_keywords(payload: Any, keywords: tuple[str, ...]) -> float | None:
    for key, value in flatten_values(payload).items():
        key_lower = key.lower()
        if all(keyword in key_lower for keyword in keywords):
            number = float_or_none(value)
            if number is not None:
                return number
    return None


def selected_numeric_metrics(payload: Any, keywords: tuple[str, ...]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for key, value in flatten_values(payload).items():
        key_lower = key.lower()
        if not any(keyword in key_lower for keyword in keywords):
            continue
        number = float_or_none(value)
        if number is not None:
            metrics[key] = number
    return metrics


def iter_position_rows(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        for key in ("items", "positions", "data", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, Mapping)]
        if "symbol" in payload:
            return [payload]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    return []


def get_first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    lower_map = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        lowered = key.lower()
        if lowered in lower_map:
            return lower_map[lowered]
    return None
