from datetime import UTC, date, datetime, time, timedelta

import pytest
from pydantic import ValidationError

from guruji.astro import (
    ENGINE_VERSION,
    BirthInput,
    Dossier,
    OutOfRangeError,
    Sky,
    compute_dossier,
    transit_snapshot,
)
from guruji.astro.chart import sign_index
from guruji.astro.constants import Graha
from guruji.astro.dasha import dasha_at
from guruji.astro.transits import sade_sati_windows
from guruji.geo.tz import birth_moment

NOW = datetime(2026, 9, 28, tzinfo=UTC)
DELHI = (28.6139, 77.2090)


def _birth(day: date, at: time | None, lat: float = DELHI[0], lon: float = DELHI[1]) -> BirthInput:
    return BirthInput(
        moment=birth_moment(day, at, "Asia/Kolkata"),
        latitude=lat,
        longitude=lon,
        time_known=at is not None,
    )


@pytest.fixture(scope="module")
def dossier(sky: Sky) -> Dossier:
    return compute_dossier(sky, _birth(date(1990, 7, 15), time(9, 0)), NOW)


def test_dossier_contents(dossier: Dossier) -> None:
    assert dossier.engine_version == ENGINE_VERSION
    assert dossier.time_known and dossier.uncertain == []
    assert dossier.d1.lagna is not None and dossier.d1.lagna.sign == "Leo"
    assert dossier.d1.graha(Graha.MOON).nakshatra == "Revati"
    assert dossier.d1.graha(Graha.SATURN).retrograde
    assert dossier.d9.lagna_sign is not None
    assert dossier.panchang.vara == "Ravivara"
    assert dossier.dasha.birth_lord is Graha.MERCURY
    assert len(dossier.dasha.mahadashas) == 9
    assert {y.name for y in dossier.yogas} >= {"Gajakesari"}
    assert 23.7 < dossier.ayanamsa < 23.8


def test_dossier_json_roundtrip(dossier: Dossier) -> None:
    raw = dossier.model_dump_json()
    assert len(raw) < 40_000
    assert Dossier.model_validate_json(raw) == dossier


def test_running_dasha(dossier: Dossier) -> None:
    chain = dasha_at(dossier.dasha.mahadashas, NOW)
    assert [p.lord for p in chain] == [Graha.SUN, Graha.JUPITER]  # Sun MD from Jun 2024


def test_sade_sati_for_pisces_moon(dossier: Dossier) -> None:
    """Sidereal Saturn entered Aquarius on 29 Apr 2022 and leaves Aries in 2030."""
    assert dossier.d1.graha(Graha.MOON).sign == "Pisces"
    current = next(s for s in dossier.sade_sati if s.start.year == 2022)
    assert current.start.date() == date(2022, 4, 29)
    assert current.end.year == 2030
    starts = [s.start for s in dossier.sade_sati]
    assert starts == sorted(starts)
    assert all(s.end - s.start < timedelta(days=9 * 365) for s in dossier.sade_sati)


def test_sade_sati_running_at_start_keeps_true_start(sky: Sky) -> None:
    windows = sade_sati_windows(sky, 11, datetime(2025, 1, 1, tzinfo=UTC), NOW)
    assert [w.start.date() for w in windows] == [date(2022, 4, 29)]


def test_transit_snapshot(sky: Sky, dossier: Dossier) -> None:
    snap = transit_snapshot(sky, dossier.d1, NOW)
    saturn = snap.graha(Graha.SATURN)
    assert saturn.sign == "Pisces"
    assert saturn.house_from_moon == 1
    assert snap.sade_sati_phase == "peak"
    lagna = dossier.d1.lagna
    assert lagna is not None
    assert saturn.house_from_lagna == (sign_index(saturn.longitude) - lagna.sign_index) % 12 + 1


def test_unknown_birth_time(sky: Sky) -> None:
    d = compute_dossier(sky, _birth(date(1990, 7, 15), None), NOW)
    assert d.d1.lagna is None and d.d9.lagna_sign is None
    assert d.d1.house_basis == "moon"
    assert d.d1.graha(Graha.MOON).house == 1
    assert "dasha_dates" in d.uncertain
    # The Moon crossed from Revati (Pisces) into Ashwini (Aries) that day.
    assert {"moon_sign", "moon_nakshatra"} <= set(d.uncertain)
    assert not any(y.name in {"Hamsa", "Malavya", "Sasa", "Ruchaka", "Bhadra"} for y in d.yogas)


def test_rejects_naive_and_out_of_range(sky: Sky) -> None:
    with pytest.raises(ValidationError):
        BirthInput(moment=datetime(1990, 1, 1, 12), latitude=0, longitude=0)
    with pytest.raises(OutOfRangeError):
        compute_dossier(sky, _birth(date(1820, 1, 1), time(12, 0)), NOW)
    with pytest.raises(OutOfRangeError):
        compute_dossier(sky, _birth(date(1990, 1, 1), time(12, 0), lat=70.0, lon=20.0), NOW)


def test_deterministic(sky: Sky, dossier: Dossier) -> None:
    again = compute_dossier(sky, _birth(date(1990, 7, 15), time(9, 0)), NOW)
    assert again == dossier
