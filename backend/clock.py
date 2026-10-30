"""
One clock for all the rules, so tests can move time. Datetimes are naive UTC everywhere (SQLite keeps no zone).
"""
from datetime import datetime, timedelta, timezone


class Clock:
    def now(self):
        return datetime.now(timezone.utc).replace(tzinfo=None)


class FakeClock(Clock):
    def __init__(self, start=None):
        self.t = start or datetime(2026, 10, 30, 12, 0, 0)

    def now(self):
        return self.t

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


_clock = Clock()


def now():
    return _clock.now()


def set_clock(clock):
    global _clock
    _clock = clock


def in_seconds(s):
    return now() + timedelta(seconds=s)


def iso(dt):
    """JSON form of a stored time: always UTC, with the Z."""
    return None if dt is None else dt.isoformat(timespec="milliseconds") + "Z"


def epoch(dt):
    """Seconds since 1970 of a stored (UTC) time."""
    return dt.replace(tzinfo=timezone.utc).timestamp()


def local_hhmm(dt):
    """A stored time as the clock on the wall in Algeria shows it."""
    from zoneinfo import ZoneInfo
    return dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Africa/Algiers")).strftime("%H:%M")
