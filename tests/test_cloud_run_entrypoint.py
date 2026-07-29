from entrypoints import cloud_run


def test_market_hours_after_close_is_closed_without_calendar_error(monkeypatch):
    class FakeSchedule:
        empty = False

    class FakeCalendar:
        def schedule(self, **_kwargs):
            return FakeSchedule()

        def open_at_time(self, _schedule, _now):
            raise ValueError("The provided timestamp is not covered by the schedule")

    monkeypatch.setattr(cloud_run.mcal, "get_calendar", lambda _name: FakeCalendar())

    assert cloud_run.is_market_open_now() == (False, None)
