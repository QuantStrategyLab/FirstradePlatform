"""Cloud Run request helpers for FirstradePlatform."""

from __future__ import annotations

from datetime import datetime

import pandas_market_calendars as mcal
import pytz


def is_market_open_now(*, calendar_name="NYSE", timezone_name="America/New_York"):
    """Return whether the US equity regular session is open right now."""
    try:
        calendar = mcal.get_calendar(calendar_name)
        market_tz = pytz.timezone(timezone_name)
        now_market = datetime.now(market_tz)
        schedule = calendar.schedule(start_date=now_market.date(), end_date=now_market.date())
        if schedule.empty:
            return False, None
        try:
            return calendar.open_at_time(schedule, now_market), None
        except ValueError as exc:
            if "not covered by the schedule" not in str(exc):
                raise
            return False, None
    except Exception as exc:
        return False, exc
