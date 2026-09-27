"""Local birth date/time → timezone-aware moment, with historical offsets from tzdata.

India alone has used LMT, Bombay/Calcutta/Madras time and wartime +06:30 before settling
on IST, so always go through the IANA zone for the birthplace, never a fixed +05:30.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NOON = time(12, 0)


class UnknownTimezoneError(ValueError):
    pass


def birth_moment(day: date, at: time | None, tz_name: str) -> datetime:
    """Aware datetime for a local birth date and time (local noon when the time is unknown).

    Clock times that occurred twice (clocks set back) resolve to the first occurrence;
    times skipped by a clock change are read with the offset in force before it.
    """
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise UnknownTimezoneError(tz_name) from e
    return datetime.combine(day, at or NOON, tz)
