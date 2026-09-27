from datetime import UTC, date, time, timedelta

import pytest

from guruji.geo.tz import UnknownTimezoneError, birth_moment


def _offset(day: date, at: time | None, tz: str) -> timedelta | None:
    return birth_moment(day, at, tz).utcoffset()


def test_indian_historical_offsets() -> None:
    assert _offset(date(1990, 1, 1), time(9, 0), "Asia/Kolkata") == timedelta(hours=5, minutes=30)
    # Wartime IST (+06:30), Sep 1942 - Oct 1945
    assert _offset(date(1943, 3, 10), time(10, 0), "Asia/Kolkata") == timedelta(hours=6, minutes=30)
    # Madras time (+05:21:10) before 1906
    assert _offset(date(1901, 2, 3), time(5, 30), "Asia/Kolkata") == timedelta(
        hours=5, minutes=21, seconds=10
    )


def test_unknown_time_is_local_noon() -> None:
    m = birth_moment(date(2000, 6, 1), None, "Asia/Kolkata")
    assert (m.hour, m.minute) == (12, 0)
    assert m.astimezone(UTC).hour == 6


def test_dst_fold_and_gap() -> None:
    # 01:30 happened twice in London on 2021-10-31; take the first (BST)
    assert _offset(date(2021, 10, 31), time(1, 30), "Europe/London") == timedelta(hours=1)
    # 02:30 never happened in New York on 2021-03-14; read with the offset before (EST)
    assert _offset(date(2021, 3, 14), time(2, 30), "America/New_York") == timedelta(hours=-5)


def test_unknown_zone() -> None:
    with pytest.raises(UnknownTimezoneError):
        birth_moment(date(2000, 1, 1), None, "Mars/Olympus_Mons")
