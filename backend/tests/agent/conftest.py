from collections.abc import Iterator
from pathlib import Path

import pytest

from guruji.geo.places import ADMIN1_FILE, CITIES_FILE, COUNTRY_FILE, PlaceIndex, load_places

# (id, name, ascii, alternates, lat, lon, country, admin1, population, tz)
_CITIES = [
    (
        1273874,
        "Kochi",
        "Kochi",
        "Cochin,Kochi,കൊച്ചി",
        9.93988,
        76.26022,
        "IN",
        "13",
        604696,
        "Asia/Kolkata",
    ),
    (1859964, "Kochi", "Kochi", "", 33.55, 133.53, "JP", "39", 330000, "Asia/Tokyo"),
    (
        1278149,
        "Aurangabad",
        "Aurangabad",
        "",
        19.87757,
        75.34226,
        "IN",
        "16",
        1016441,
        "Asia/Kolkata",
    ),
    (1278148, "Aurangābād", "Aurangabad", "", 24.75204, 84.3742, "IN", "34", 95929, "Asia/Kolkata"),
    (
        1273294,
        "Delhi",
        "Delhi",
        "Dilli,दिल्ली",
        28.65195,
        77.23149,
        "IN",
        "07",
        11034555,
        "Asia/Kolkata",
    ),
    (
        2643743,
        "London",
        "London",
        "Londres",
        51.50853,
        -0.12574,
        "GB",
        "ENG",
        8961989,
        "Europe/London",
    ),
    (1234567, "Tromsø", "Tromso", "", 69.6496, 18.95508, "NO", "18", 77000, "Europe/Oslo"),
]
_ADMIN1 = {
    "IN.13": "Kerala",
    "JP.39": "Kochi",
    "IN.16": "Maharashtra",
    "IN.34": "Bihar",
    "IN.07": "Delhi",
    "GB.ENG": "England",
    "NO.18": "Troms",
}
_COUNTRIES = {"IN": "India", "JP": "Japan", "GB": "United Kingdom", "NO": "Norway"}


def write_geonames(d: Path) -> Path:
    rows = []
    for gid, name, ascii_, alt, lat, lon, cc, a1, pop, tz in _CITIES:
        cols = [
            str(gid),
            name,
            ascii_,
            alt,
            str(lat),
            str(lon),
            "P",
            "PPL",
            cc,
            "",
            a1,
            "",
            "",
            "",
            str(pop),
            "",
            "10",
            tz,
            "2024-01-01",
        ]
        rows.append("\t".join(cols))
    (d / CITIES_FILE).write_text("\n".join(rows) + "\n", "utf-8")
    (d / ADMIN1_FILE).write_text(
        "\n".join(f"{k}\t{v}\t{v}\t1" for k, v in _ADMIN1.items()) + "\n", "utf-8"
    )
    (d / COUNTRY_FILE).write_text(
        "#ISO\tISO3\tISO-Numeric\tfips\tCountry\n"
        + "\n".join(f"{k}\tXXX\t000\tXX\t{v}" for k, v in _COUNTRIES.items())
        + "\n",
        "utf-8",
    )
    return d


@pytest.fixture(scope="session")
def places(tmp_path_factory: pytest.TempPathFactory) -> Iterator[PlaceIndex]:
    yield load_places(write_geonames(tmp_path_factory.mktemp("geonames")))
