"""Sidereal positions: JPL ephemeris via Skyfield, Lahiri ayanamsa, mean lunar node.

Conventions (checked against Swiss Ephemeris in tests/astro/test_golden.py):
- Civil birth times are UT (UT1), as in every astrology package; Skyfield's ΔT gives TT.
- Planets are apparent geocentric positions (light-time, aberration, deflection) on the
  true ecliptic of date; sidereal = tropical - (mean ayanamsa + nutation in longitude).
- Lahiri (Chitrapaksha) ayanamsa is the Swiss Ephemeris definition: the vernal point of
  1956-03-21 (JD 2435553.5 TT) sits at 23.245524743° and is precessed to the date.
- Rahu is the mean node; Ketu is exactly opposite.
- The ascendant uses apparent sidereal time and the true obliquity.

Pure maths: the caller supplies an already loaded `Sky` (see guruji.ephemeris).
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from numpy.typing import NDArray
from skyfield import almanac, nutationlib, precessionlib
from skyfield.api import wgs84
from skyfield.framelib import ecliptic_frame
from skyfield.jpllib import SpiceKernel
from skyfield.timelib import Time, Timescale

from guruji.astro.constants import Graha

J2000 = 2451545.0
_J2000_DT = datetime(2000, 1, 1, 12, tzinfo=UTC)
_ARCSEC = math.pi / (180 * 3600)

LAHIRI_T0 = 2435553.5
LAHIRI_AYANAMSA_T0 = 23.250182778 - 0.004658035

# DE440s covers 1849-12-26 .. 2150-01-22; keep a margin for speeds and searches.
MIN_JD = 2396760.5  # 1850-01-02
MAX_JD = 2506340.5  # 2150-01-09
MAX_ABS_LATITUDE = 66.0  # whole-sign lagna is ill-defined inside the polar circles

_BODIES: dict[Graha, str] = {
    Graha.SUN: "sun",
    Graha.MOON: "moon",
    Graha.MARS: "mars barycenter",
    Graha.MERCURY: "mercury",
    Graha.JUPITER: "jupiter barycenter",
    Graha.VENUS: "venus",
    Graha.SATURN: "saturn barycenter",
}
_SPEED_STEP_DAYS = 1.0 / 24

# Vernal point of the Lahiri epoch, in J2000 equatorial coordinates.
_LAHIRI_V0: NDArray[np.float64] = precessionlib.compute_precession(LAHIRI_T0)[:, 0]


class OutOfRangeError(ValueError):
    """The requested date or place is outside what the engine supports."""


@dataclass(frozen=True)
class Sky:
    """A loaded ephemeris and timescale. Build it with `guruji.ephemeris.load_sky()`."""

    ts: Timescale
    kernel: SpiceKernel

    def at(self, jd_ut: float | NDArray[np.float64]) -> Time:
        return self.ts.ut1_jd(jd_ut)


@dataclass(frozen=True)
class GrahaState:
    longitude: float  # sidereal degrees, [0, 360)
    speed: float  # degrees per day; negative means retrograde


def julian_day(when: datetime) -> float:
    """Julian day (UT) of a timezone-aware datetime."""
    if when.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    jd = J2000 + (when - _J2000_DT).total_seconds() / 86400
    if not MIN_JD <= jd <= MAX_JD:
        raise OutOfRangeError("date outside the supported range (1850-2149)")
    return jd


def datetime_from_jd(jd_ut: float) -> datetime:
    """UTC datetime of a Julian day (UT), to the nearest second."""
    return _J2000_DT + timedelta(seconds=round((jd_ut - J2000) * 86400))


def norm(deg: Any) -> Any:
    """Normalise degrees to [0, 360); works on floats and arrays."""
    return np.mod(deg, 360.0)


def signed_diff(a: float, b: float) -> float:
    """Shortest signed angle a - b, in (-180, 180]."""
    d = (a - b) % 360.0
    return d - 360.0 if d > 180.0 else d


def ayanamsa_mean(t: Time) -> Any:
    """Lahiri ayanamsa without nutation, in degrees (float or array like `t`)."""
    p = precessionlib.compute_precession(t.tdb)  # J2000 → mean equator of date
    v = np.einsum("ji...,j->i...", p, _LAHIRI_V0)  # transpose: rotate into date frame
    eps = nutationlib.mean_obliquity(t.tdb) * _ARCSEC
    y = v[1] * np.cos(eps) + v[2] * np.sin(eps)
    return LAHIRI_AYANAMSA_T0 - np.degrees(np.arctan2(y, v[0]))


def ayanamsa_true(t: Time) -> Any:
    dpsi, _ = nutationlib.iau2000a_radians(t)
    return ayanamsa_mean(t) + np.degrees(dpsi)


def _tropical_longitude(sky: Sky, graha: Graha, t: Time) -> Any:
    earth = sky.kernel["earth"]
    _, lon, _ = (
        earth.at(t).observe(sky.kernel[_BODIES[graha]]).apparent().frame_latlon(ecliptic_frame)
    )
    return lon.degrees


def _mean_node_tropical(t: Time) -> Any:
    """Mean ascending node of the Moon, mean equinox of date (Chapront et al. 1998)."""
    c = (t.tt - J2000) / 36525
    return norm(125.0445479 - 1934.1362891 * c + 0.0020754 * c**2 + c**3 / 467441 - c**4 / 60616000)


def sidereal_longitude(sky: Sky, graha: Graha, t: Time) -> Any:
    """Sidereal longitude of one graha at `t` (scalar or vector Time)."""
    if graha in (Graha.RAHU, Graha.KETU):
        rahu = norm(_mean_node_tropical(t) - ayanamsa_mean(t))
        return rahu if graha is Graha.RAHU else norm(rahu + 180.0)
    return norm(_tropical_longitude(sky, graha, t) - ayanamsa_true(t))


def graha_states(sky: Sky, jd_ut: float) -> dict[Graha, GrahaState]:
    """Sidereal longitude and daily motion of all nine grahas."""
    h = _SPEED_STEP_DAYS
    t3 = sky.at(np.array([jd_ut - h, jd_ut, jd_ut + h]))
    t = sky.at(jd_ut)
    ayan = float(ayanamsa_true(t))
    out: dict[Graha, GrahaState] = {}
    for graha in _BODIES:
        lons = _tropical_longitude(sky, graha, t3)
        speed = signed_diff(float(lons[2]), float(lons[0])) / (2 * h)
        out[graha] = GrahaState(float(norm(lons[1] - ayan)), speed)
    nodes = _mean_node_tropical(t3)
    node_speed = signed_diff(float(nodes[2]), float(nodes[0])) / (2 * h)
    rahu = float(sidereal_longitude(sky, Graha.RAHU, t))
    out[Graha.RAHU] = GrahaState(rahu, node_speed)
    out[Graha.KETU] = GrahaState(float(norm(rahu + 180.0)), node_speed)
    return out


def ascendant(sky: Sky, jd_ut: float, latitude: float, longitude: float) -> float:
    """Sidereal longitude of the lagna (rising point of the ecliptic)."""
    if abs(latitude) > MAX_ABS_LATITUDE:
        raise OutOfRangeError("latitude too close to the poles for a lagna")
    t = sky.at(jd_ut)
    dpsi, deps = nutationlib.iau2000a_radians(t)
    eps = float(nutationlib.mean_obliquity(t.tdb)) * _ARCSEC + float(deps)
    ramc = math.radians((float(t.gast) * 15.0 + longitude) % 360.0)
    phi = math.radians(latitude)
    asc = math.degrees(
        math.atan2(
            math.cos(ramc),
            -(math.sin(ramc) * math.cos(eps) + math.tan(phi) * math.sin(eps)),
        )
    )
    return float(norm(asc - float(ayanamsa_mean(t)) - math.degrees(float(dpsi))))


def previous_sunrise(sky: Sky, jd_ut: float, latitude: float, longitude: float) -> float | None:
    """JD (UT) of the last sunrise at or before `jd_ut`, or None if the Sun did not rise."""
    place = sky.kernel["earth"] + wgs84.latlon(latitude, longitude)
    t0, t1 = sky.at(jd_ut - 1.5), sky.at(jd_ut)
    times, rose = almanac.find_risings(place, sky.kernel["sun"], t0, t1)
    risen = [float(tt.ut1) for tt, ok in zip(times, rose, strict=True) if ok]
    return risen[-1] if risen else None
