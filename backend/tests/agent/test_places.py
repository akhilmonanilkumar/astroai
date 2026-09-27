from guruji.geo.places import PlaceIndex, normalize


def _labels(places: PlaceIndex, q: str) -> list[str]:
    return [p.label for p in places.search(q)]


def test_prefers_india_then_population(places: PlaceIndex) -> None:
    assert _labels(places, "Kochi") == ["Kochi, Kerala, India", "Kochi, Japan"]
    assert _labels(places, "Aurangabad")[0] == "Aurangabad, Maharashtra, India"


def test_alternate_names_scripts_and_hints(places: PlaceIndex) -> None:
    assert _labels(places, "cochin") == ["Kochi, Kerala, India"]
    assert _labels(places, "दिल्ली") == ["Delhi, India"]
    assert _labels(places, "Aurangabad, Bihar") == ["Aurangābād, Bihar, India"]
    assert _labels(places, "kochi, japan") == ["Kochi, Japan"]


def test_fuzzy_and_misses(places: PlaceIndex) -> None:
    assert _labels(places, "Lndon") == ["London, England, United Kingdom"]
    assert _labels(places, "xyzzyq") == []
    assert _labels(places, "  ") == []


def test_place_carries_timezone(places: PlaceIndex) -> None:
    p = places.search("Kochi")[0]
    assert (p.tz_name, round(p.latitude, 2)) == ("Asia/Kolkata", 9.94)


def test_normalize() -> None:
    assert normalize("  Chennai District. ") == "chennai"
