from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from guruji.astro.constants import DASHA_YEAR_DAYS, Graha
from guruji.astro.dasha import balance_years, birth_dasha, dasha_at, vimshottari

BIRTH = datetime(1990, 1, 1, tzinfo=UTC)


def _years(d: timedelta) -> float:
    return d.total_seconds() / 86400 / DASHA_YEAR_DAYS


@pytest.mark.parametrize(
    ("moon", "lord", "balance"),
    [
        (0.0, Graha.KETU, 7.0),  # start of Ashwini
        (20.0 / 3, Graha.KETU, 3.5),  # middle of Ashwini
        (40.0 / 3, Graha.VENUS, 20.0),  # start of Bharani
        (350.0, Graha.MERCURY, 12.75),  # Revati, a quarter gone
        (120.0 + 20.0 / 3, Graha.KETU, 3.5),  # Magha repeats the Ketu cycle
    ],
)
def test_birth_balance(moon: float, lord: Graha, balance: float) -> None:
    assert birth_dasha(moon)[0] is lord
    assert balance_years(moon) == pytest.approx(balance)


def test_mahadashas_cover_120_years_contiguously() -> None:
    mds = vimshottari(20.0 / 3, BIRTH)
    assert [m.lord for m in mds] == [
        Graha.KETU,
        Graha.VENUS,
        Graha.SUN,
        Graha.MOON,
        Graha.MARS,
        Graha.RAHU,
        Graha.JUPITER,
        Graha.SATURN,
        Graha.MERCURY,
    ]
    assert _years(BIRTH - mds[0].start) == pytest.approx(3.5)
    assert _years(mds[0].end - BIRTH) == pytest.approx(3.5)
    assert _years(mds[-1].end - mds[0].start) == pytest.approx(120.0)
    for a, b in pairwise(mds):
        assert a.end == b.start


def test_antardashas() -> None:
    venus = vimshottari(40.0 / 3, BIRTH)[0]
    assert venus.lord is Graha.VENUS
    subs = venus.sub
    assert subs[0].lord is Graha.VENUS
    assert subs[1].lord is Graha.SUN
    assert _years(subs[0].end - subs[0].start) == pytest.approx(20 * 20 / 120)
    assert subs[0].start == venus.start
    assert subs[-1].end == venus.end
    assert all(s.sub == [] for s in subs)


def test_dasha_at_and_levels() -> None:
    mds = vimshottari(40.0 / 3, BIRTH, levels=3)
    chain = dasha_at(mds, BIRTH + timedelta(days=1))
    assert [p.lord for p in chain] == [Graha.VENUS, Graha.VENUS, Graha.VENUS]
    later = dasha_at(mds, BIRTH + timedelta(days=21 * DASHA_YEAR_DAYS))
    assert later[0].lord is Graha.SUN
    assert dasha_at(mds, BIRTH + timedelta(days=200 * 365)) == []
    with pytest.raises(ValueError):
        vimshottari(0.0, BIRTH, levels=0)
