"""
Liquidity Radar — silent hours.

    python test_notify.py

The pipeline runs FULLY during silent hours: items are fetched, classified,
clustered, enriched and written to the site exactly as always. The only thing
that changes is that Telegram stays quiet. Getting that distinction wrong in
either direction is bad — a silenced PIPELINE loses deals, and a silenced
OUTAGE WARNING means finding out twelve hours late that nothing has run.
"""
import sys
from datetime import datetime

import config
import notify

IST = notify.IST
FAILURES = []


def check(name, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(name); print(f"  FAIL  {name}\n        {exc}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(name); print(f"  ERROR {name}\n        {type(exc).__name__}: {exc}")
    else:
        print(f"  ok    {name}")


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


# --------------------------------------------------------------------------
# The window, including the edges and the midnight wrap.
# --------------------------------------------------------------------------
def test_working_hours_send():
    for hh in (8, 9, 12, 15, 17):
        assert not notify.in_quiet_hours(at(2026, 9, 21, hh)), hh


def test_evening_and_night_are_silent():
    for hh in (18, 19, 22, 23, 0, 3, 6, 7):
        assert notify.in_quiet_hours(at(2026, 9, 22, hh)), hh


def test_the_boundaries_are_where_they_claim():
    """18:00 is silent, 17:59 is not; 08:00 sends, 07:59 does not."""
    assert not notify.in_quiet_hours(at(2026, 9, 21, 17, 59))
    assert notify.in_quiet_hours(at(2026, 9, 21, 18, 0))
    assert notify.in_quiet_hours(at(2026, 9, 21, 7, 59))
    assert not notify.in_quiet_hours(at(2026, 9, 21, 8, 0))


def test_weekends_are_silent_all_day():
    for day in (26, 27):          # Sat, Sun
        for hh in (9, 12, 16):
            assert notify.in_quiet_hours(at(2026, 9, day, hh)), (day, hh)


def test_friday_evening_through_monday_morning_is_one_silence():
    assert notify.in_quiet_hours(at(2026, 9, 25, 18))     # Fri 18:00
    assert notify.in_quiet_hours(at(2026, 9, 26, 12))     # Sat noon
    assert notify.in_quiet_hours(at(2026, 9, 28, 7, 30))  # Mon 07:30
    assert not notify.in_quiet_hours(at(2026, 9, 28, 8))  # Mon 08:00


# --------------------------------------------------------------------------
# What is silenced, and what must never be.
# --------------------------------------------------------------------------
def _sent_during(quiet, fn):
    """Run fn() with in_quiet_hours forced, capturing whether send() fired."""
    calls = []
    orig_q, orig_s = notify.in_quiet_hours, notify.send
    notify.in_quiet_hours = lambda *a, **k: quiet
    notify.send = lambda *a, **k: calls.append(a) or True
    try:
        fn()
    finally:
        notify.in_quiet_hours, notify.send = orig_q, orig_s
    return bool(calls)


ALERT = {"deal_id": 1, "company": "X", "deal_type": "block deal", "amount_cr": 900,
         "one_line": "x", "individuals": [], "seller": "p", "source": "ET",
         "url": "http://x", "confidence": "high", "size_source": "stated"}


def test_deal_alerts_are_silenced():
    assert not _sent_during(True, lambda: notify.send_alert(dict(ALERT)))
    assert _sent_during(False, lambda: notify.send_alert(dict(ALERT)))


def test_pattern_alerts_are_silenced():
    trades = [("2026-06-01", 120.0), ("2026-06-20", 120.0)]
    call = lambda: notify.send_pattern_alert("A Person", "Co", 900, trades, 3)
    assert not _sent_during(True, call)
    assert _sent_during(False, call)


def test_operational_messages_are_never_silenced():
    """A classifier outage or a failed run is about the SYSTEM being broken,
    not about a deal. Those go through send() directly, and learning about
    them twelve hours late is worse than being interrupted."""
    import inspect
    body = inspect.getsource(notify.send)
    assert "in_quiet_hours" not in body, (
        "send() must stay raw — silencing it would also silence the "
        "classifier-down warning and the daily digest")


def test_a_silenced_alert_reports_itself():
    """A quiet night must look deliberate in the log, not like a broken bot."""
    import inspect
    assert "quiet hours" in inspect.getsource(notify._quiet)


def test_the_window_is_what_was_asked_for():
    assert config.QUIET_START_HOUR == 18
    assert config.QUIET_END_HOUR == 8
    assert tuple(config.QUIET_DAYS) == (5, 6), "Python weekday: Sat=5, Sun=6"


if __name__ == "__main__":
    print("silent hours\n")
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            check(_n[5:].replace("_", " "), _f)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED"); sys.exit(1)
    print("all passed")
