"""Engine vs Swiss Ephemeris on 50 golden charts (see make_golden.py)."""

import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest

from guruji.astro import Sky
from guruji.astro.chart import nakshatra_index, navamsa_sign, sign_index
from guruji.astro.constants import NAKSHATRA_SPAN, PADA_SPAN, Graha
from guruji.astro.sky import (
    ascendant,
    ayanamsa_true,
    graha_states,
    julian_day,
    signed_diff,
)
from guruji.geo.tz import birth_moment

GOLDEN = json.loads((Path(__file__).parent / "golden" / "charts.json").read_text("utf-8"))
CHARTS: list[dict[str, Any]] = GOLDEN["charts"]

# Arc-seconds. Measured worst cases: Moon 0.56", everything else 0.15" (precession and
# ΔT models differ slightly). The Moon and lagna move fastest, so ΔT shows there first.
TOL_ARCSEC = {Graha.MOON: 1.5}
TOL_DEFAULT_ARCSEC = 0.5
TOL_ASC_ARCSEC = 1.0
TOL_AYANAMSA_ARCSEC = 0.3
TOL_SPEED = 1e-3  # degrees/day


def _moment(c: dict[str, Any]) -> datetime:
    local = datetime.fromisoformat(c["local"])
    return birth_moment(local.date(), local.time(), c["tz"])


def _arcsec(a: float, b: float) -> float:
    return abs(signed_diff(a, b)) * 3600


def _near_boundary(lon: float, span: float, tol_deg: float) -> bool:
    r = lon % span
    return r < tol_deg or span - r < tol_deg


def test_fixture_size() -> None:
    assert len(CHARTS) >= 50


@pytest.mark.parametrize("c", CHARTS, ids=[c["id"] for c in CHARTS])
def test_timezone_conversion(c: dict[str, Any]) -> None:
    assert _moment(c).astimezone(UTC) == datetime.fromisoformat(c["utc"])


@pytest.mark.parametrize("c", CHARTS, ids=[c["id"] for c in CHARTS])
def test_positions_match_swiss_ephemeris(sky: Sky, c: dict[str, Any]) -> None:
    jd = julian_day(_moment(c))
    assert _arcsec(float(ayanamsa_true(sky.at(jd))), c["ayanamsa"]) < TOL_AYANAMSA_ARCSEC

    states = graha_states(sky, jd)
    for g in Graha:
        want = c["grahas"][g.value]
        got = states[g]
        tol = TOL_ARCSEC.get(g, TOL_DEFAULT_ARCSEC)
        assert _arcsec(got.longitude, want["longitude"]) < tol, g
        assert got.speed == pytest.approx(want["speed"], abs=TOL_SPEED), g

    asc = ascendant(sky, jd, c["latitude"], c["longitude"])
    assert _arcsec(asc, c["ascendant"]) < TOL_ASC_ARCSEC


@pytest.mark.parametrize("c", CHARTS, ids=[c["id"] for c in CHARTS])
def test_divisions_match_swiss_ephemeris(sky: Sky, c: dict[str, Any]) -> None:
    """Sign, nakshatra, pada and navamsa agree unless the oracle sits on a boundary."""
    jd = julian_day(_moment(c))
    states = graha_states(sky, jd)
    points = {g.value: (states[g].longitude, c["grahas"][g.value]["longitude"]) for g in Graha}
    points["Lagna"] = (ascendant(sky, jd, c["latitude"], c["longitude"]), c["ascendant"])
    tol = TOL_ASC_ARCSEC / 3600
    for name, (got, want) in points.items():
        if not _near_boundary(want, 30.0, tol):
            assert sign_index(got) == sign_index(want), name
        if not _near_boundary(want, NAKSHATRA_SPAN, tol):
            assert nakshatra_index(got) == nakshatra_index(want), name
        if not _near_boundary(want, PADA_SPAN, tol):
            assert int(got // PADA_SPAN) == int(want // PADA_SPAN), name
            assert navamsa_sign(got) == navamsa_sign(want), name


@pytest.mark.parametrize("c", CHARTS, ids=[c["id"] for c in CHARTS])
def test_retrograde_flags(sky: Sky, c: dict[str, Any]) -> None:
    states = graha_states(sky, julian_day(_moment(c)))
    for g in Graha:
        want = c["grahas"][g.value]["speed"]
        if abs(want) > TOL_SPEED:
            assert (states[g].speed < 0) == (want < 0), g


def test_known_chart_independence(sky: Sky) -> None:
    """India, 1947-08-15 00:00 Delhi: Taurus lagna, Moon in Pushya, five grahas in Cancer."""
    jd = julian_day(birth_moment(date(1947, 8, 15), time(0, 0), "Asia/Kolkata"))
    states = graha_states(sky, jd)
    assert sign_index(ascendant(sky, jd, 28.6139, 77.2090)) == 1
    assert nakshatra_index(states[Graha.MOON].longitude) == 7
    in_cancer = {g for g, s in states.items() if sign_index(s.longitude) == 3}
    assert in_cancer == {Graha.SUN, Graha.MOON, Graha.MERCURY, Graha.VENUS, Graha.SATURN}
