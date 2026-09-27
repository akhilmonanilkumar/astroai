from guruji.astro.chart import RashiChart
from guruji.astro.yogas import Yoga, find_yogas

from .helpers import chart


def _yogas(c: RashiChart) -> dict[str, Yoga]:
    return {y.name: y for y in find_yogas(c)}


def test_base_chart_is_quiet() -> None:
    names = set(_yogas(chart()))
    assert names.isdisjoint({"Gajakesari", "Chandra-Mangala", "Kaal Sarp", "Guru Chandal"})


def test_gajakesari_counts_from_the_moon() -> None:
    y = _yogas(chart(Moon=105.0, Jupiter=195.0))  # Cancer -> Libra = 4th
    assert y["Gajakesari"].detail == "Jupiter in the 4th house from the Moon"
    assert "Gajakesari" not in _yogas(chart(Moon=105.0, Jupiter=135.0))  # 2nd


def test_budhaditya_and_chandra_mangala() -> None:
    y = _yogas(chart(Sun=45.0, Mercury=55.0, Moon=200.0, Mars=205.0))
    assert "Budhaditya" in y
    assert y["Chandra-Mangala"].detail == "Moon and Mars together in Libra"


def test_mahapurusha_needs_lagna_kendra_and_strength() -> None:
    # Jupiter exalted in Cancer, lagna Aries -> 4th house: Hamsa
    y = _yogas(chart(ascendant=5.0, Jupiter=95.0))
    assert y["Hamsa"].detail == "Jupiter exalted in Cancer, 4th house from the lagna"
    assert "Hamsa" not in _yogas(chart(ascendant=35.0, Jupiter=95.0))  # 3rd house
    assert "Hamsa" not in _yogas(chart(ascendant=None, Jupiter=95.0))  # no lagna


def test_moon_flank_yogas() -> None:
    # Base chart: Moon in Cancer; Venus in Gemini (12th) -> Anapha
    assert "Anapha" in _yogas(chart())
    both = _yogas(chart(Saturn=135.0))  # Saturn to Leo (2nd)
    assert both["Durdhara"].grahas == ["Saturn", "Venus"]
    kema = _yogas(chart(Venus=200.0))  # nothing on either side
    assert kema["Kemadruma"].kind == "dosha"
    # The Sun next to the Moon does not count
    assert "Kemadruma" in _yogas(chart(Venus=200.0, Sun=80.0))


def test_manglik_from_lagna_and_moon() -> None:
    y = _yogas(chart(ascendant=5.0, Moon=105.0, Mars=215.0))  # Scorpio: 8th / 5th
    assert y["Manglik"].detail == "Mars in the 8th house from the lagna"
    y = _yogas(chart(ascendant=5.0, Moon=105.0, Mars=275.0))  # Capricorn: 10th / 7th
    assert y["Manglik"].detail == "Mars in the 7th house from the Moon"
    assert "Manglik" not in _yogas(chart(ascendant=5.0, Moon=105.0, Mars=155.0))  # 6th/3rd


def test_kaal_sarp() -> None:
    hemmed = chart(
        Rahu=10.0,
        Sun=20.0,
        Moon=40.0,
        Mars=60.0,
        Mercury=30.0,
        Jupiter=100.0,
        Venus=50.0,
        Saturn=150.0,
    )
    y = _yogas(hemmed)
    assert "from Rahu to Ketu" in y["Kaal Sarp"].detail
    assert "Kaal Sarp" not in _yogas(chart())


def test_node_conjunctions() -> None:
    y = _yogas(chart(Rahu=285.0, Jupiter=290.0, Sun=100.0))  # Rahu+Jupiter Capricorn
    assert y["Guru Chandal"].grahas == ["Jupiter", "Rahu"]
    y = _yogas(chart(Rahu=285.0, Sun=100.0))  # Ketu in Cancer with the Moon
    assert y["Grahan"].detail == "Moon with Ketu in Cancer"
