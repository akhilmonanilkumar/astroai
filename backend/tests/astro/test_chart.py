import pytest

from guruji.astro.chart import (
    Dignity,
    dignity,
    house_from,
    is_combust,
    navamsa_chart,
    navamsa_sign,
    placement,
)
from guruji.astro.constants import Graha

from .helpers import chart


@pytest.mark.parametrize(
    ("graha", "lon", "want"),
    [
        (Graha.SUN, 10.0, Dignity.EXALTED),  # Aries
        (Graha.SUN, 190.0, Dignity.DEBILITATED),  # Libra
        (Graha.SUN, 130.0, Dignity.MOOLATRIKONA),  # Leo 10°
        (Graha.SUN, 145.0, Dignity.OWN),  # Leo 25°
        (Graha.SUN, 100.0, Dignity.FRIEND),  # Cancer (Moon)
        (Graha.SUN, 70.0, Dignity.NEUTRAL),  # Gemini (Mercury)
        (Graha.SUN, 300.0, Dignity.ENEMY),  # Capricorn (Saturn)
        (Graha.MOON, 40.0, Dignity.EXALTED),  # Taurus, whole sign
        (Graha.MOON, 220.0, Dignity.DEBILITATED),  # Scorpio
        (Graha.MERCURY, 170.0, Dignity.EXALTED),  # Virgo 20°, whole sign
        (Graha.MERCURY, 345.0, Dignity.DEBILITATED),  # Pisces
        (Graha.MARS, 5.0, Dignity.MOOLATRIKONA),  # Aries 5°
        (Graha.MARS, 20.0, Dignity.OWN),  # Aries 20°
        (Graha.JUPITER, 95.0, Dignity.EXALTED),  # Cancer
        (Graha.VENUS, 185.0, Dignity.MOOLATRIKONA),  # Libra 5°
        (Graha.SATURN, 315.0, Dignity.MOOLATRIKONA),  # Aquarius 15°
        (Graha.SATURN, 20.0, Dignity.DEBILITATED),  # Aries
        (Graha.RAHU, 50.0, None),
    ],
)
def test_dignity(graha: Graha, lon: float, want: Dignity | None) -> None:
    assert dignity(graha, lon) == want


def test_combustion_orbs() -> None:
    assert is_combust(Graha.MERCURY, 113.0, 100.0, retrograde=False)  # 13° < 14°
    assert not is_combust(Graha.MERCURY, 113.0, 100.0, retrograde=True)  # 13° > 12°
    assert is_combust(Graha.VENUS, 355.0, 3.0, retrograde=False)  # across 0° Aries
    assert not is_combust(Graha.SUN, 0.0, 0.0, retrograde=False)
    assert not is_combust(Graha.RAHU, 0.0, 0.0, retrograde=True)


@pytest.mark.parametrize(
    ("lon", "sign"),
    [(0.0, 0), (3.34, 1), (29.99, 8), (30.0, 9), (60.0, 6), (90.0, 3), (359.99, 11)],
)
def test_navamsa_sign(lon: float, sign: int) -> None:
    assert navamsa_sign(lon) == sign


def test_placement_fields() -> None:
    p = placement(133.5)  # Leo 13°30'
    assert (p.sign, p.nakshatra, p.pada, p.nakshatra_lord) == ("Leo", "Purva Phalguni", 1, "Venus")
    assert p.degree == pytest.approx(13.5)
    assert placement(360.0).longitude == 0.0


def test_whole_sign_houses_and_basis() -> None:
    assert house_from(11, 0) == 12
    assert house_from(0, 11) == 2
    c = chart(ascendant=5.0)
    assert c.house_basis == "lagna"
    assert c.house_of(Graha.SUN) == 2  # Taurus from Aries
    moon_based = chart(ascendant=None)
    assert moon_based.lagna is None
    assert moon_based.house_basis == "moon"
    assert moon_based.house_of(Graha.MOON) == 1


def test_nodes_are_retrograde_and_opposite() -> None:
    c = chart()
    rahu, ketu = c.graha(Graha.RAHU), c.graha(Graha.KETU)
    assert rahu.retrograde and ketu.retrograde
    assert (ketu.longitude - rahu.longitude) % 360 == pytest.approx(180.0)


def test_vargottama() -> None:
    d9 = navamsa_chart(chart(Sun=1.0))  # Aries 1° is Aries navamsa
    assert d9.signs[Graha.SUN] == "Aries"
    assert Graha.SUN in d9.vargottama
    assert d9.lagna_sign == "Taurus"  # lagna at Aries 5° -> 2nd navamsa
