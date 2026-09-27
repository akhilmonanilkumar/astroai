from datetime import date, time

import pytest

from guruji.astro import Sky
from guruji.astro.constants import Graha
from guruji.astro.panchang import karana, nitya_yoga, tithi, vara_index
from guruji.astro.sky import julian_day
from guruji.geo.tz import birth_moment


@pytest.mark.parametrize(
    ("sun", "moon", "n", "name", "paksha"),
    [
        (0.0, 5.0, 1, "Pratipada", "Shukla"),
        (100.0, 100.0 + 179.0, 15, "Purnima", "Shukla"),
        (100.0, 100.0 + 180.5, 16, "Pratipada", "Krishna"),
        (100.0, 100.0 + 100.0, 9, "Navami", "Shukla"),
        (300.0, 299.0, 30, "Amavasya", "Krishna"),
        (0.0, 330.0, 28, "Trayodashi", "Krishna"),
    ],
)
def test_tithi(sun: float, moon: float, n: int, name: str, paksha: str) -> None:
    assert tithi(sun, moon) == (n, name, paksha)


@pytest.mark.parametrize(
    ("elongation", "name"),
    [
        (1.0, "Kimstughna"),
        (7.0, "Bava"),
        (13.0, "Balava"),
        (49.0, "Bava"),  # k=8 -> cycle restarts
        (337.0, "Vishti"),  # k=56
        (343.0, "Shakuni"),
        (349.0, "Chatushpada"),
        (355.0, "Naga"),
    ],
)
def test_karana(elongation: float, name: str) -> None:
    assert karana(10.0, 10.0 + elongation) == name


def test_nitya_yoga() -> None:
    assert nitya_yoga(0.0, 1.0) == "Vishkumbha"
    assert nitya_yoga(200.0, 155.0) == "Vaidhriti"


def test_vara_starts_at_sunrise(sky: Sky) -> None:
    """15 Jul 1990 was a Sunday; at 04:00 in Delhi the Sun had not yet risen."""
    tz_name = "Asia/Kolkata"
    before = birth_moment(date(1990, 7, 15), time(4, 0), tz_name)
    after = birth_moment(date(1990, 7, 15), time(9, 0), tz_name)
    assert before.tzinfo is not None
    wd_before = vara_index(sky, julian_day(before), 28.61, 77.21, before.tzinfo)
    wd_after = vara_index(sky, julian_day(after), 28.61, 77.21, before.tzinfo)
    assert (wd_before, wd_after) == (5, 6)  # Saturday, Sunday


def test_weekday_lord_table() -> None:
    from guruji.astro.constants import WEEKDAY_LORDS, WEEKDAYS

    assert WEEKDAYS[6] == "Ravivara" and WEEKDAY_LORDS[6] is Graha.SUN
    assert WEEKDAYS[5] == "Shanivara" and WEEKDAY_LORDS[5] is Graha.SATURN
