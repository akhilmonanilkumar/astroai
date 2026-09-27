"""Gochara (transits) against a natal chart, and Sade Sati windows."""

from datetime import datetime
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from guruji.astro.chart import RashiChart, house_from, sign_index
from guruji.astro.constants import GRAHAS, SIGNS, Graha
from guruji.astro.sky import (
    MAX_JD,
    MIN_JD,
    Sky,
    datetime_from_jd,
    graha_states,
    julian_day,
    sidereal_longitude,
)

SadeSatiPhase = Literal["rising", "peak", "setting"]
_PHASES: dict[int, SadeSatiPhase] = {12: "rising", 1: "peak", 2: "setting"}

_SCAN_STEP_DAYS = 5.0
_REFINE_OFFSETS = np.arange(1, _SCAN_STEP_DAYS * 24 + 1) / 24  # hourly, within one step
# Saturn can dip back out of the 12th-2nd span on a retrograde loop; gaps shorter than
# this are one Sade Sati. (Consecutive Sade Satis are ~22 years apart.)
_MERGE_GAP_DAYS = 3 * 365.25
# Scan this far beyond [start, end] so windows crossing either edge get their true dates.
_MARGIN_DAYS = 8 * 365.25


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class TransitPlacement(_Model):
    graha: Graha
    longitude: float
    sign: str
    retrograde: bool
    house_from_lagna: int | None
    house_from_moon: int


class TransitSnapshot(_Model):
    at: datetime
    grahas: list[TransitPlacement]
    sade_sati_phase: SadeSatiPhase | None

    def graha(self, graha: Graha) -> TransitPlacement:
        return next(p for p in self.grahas if p.graha == graha)


class SadeSati(_Model):
    start: datetime  # Saturn first enters the 12th sign from the natal Moon
    end: datetime  # Saturn finally leaves the 2nd sign from the natal Moon


def transit_snapshot(sky: Sky, natal: RashiChart, when: datetime) -> TransitSnapshot:
    states = graha_states(sky, julian_day(when))
    moon_sign = natal.graha(Graha.MOON).sign_index
    lagna_sign = natal.lagna.sign_index if natal.lagna else None
    grahas = []
    for g in GRAHAS:
        st = states[g]
        s = sign_index(st.longitude)
        grahas.append(
            TransitPlacement(
                graha=g,
                longitude=round(st.longitude, 6),
                sign=SIGNS[s],
                retrograde=st.speed < 0,
                house_from_lagna=house_from(s, lagna_sign) if lagna_sign is not None else None,
                house_from_moon=house_from(s, moon_sign),
            )
        )
    saturn = next(p for p in grahas if p.graha is Graha.SATURN)
    return TransitSnapshot(
        at=when, grahas=grahas, sade_sati_phase=_PHASES.get(saturn.house_from_moon)
    )


def _in_span(sky: Sky, jds: NDArray[np.float64], span: list[int]) -> NDArray[np.bool_]:
    lons = sidereal_longitude(sky, Graha.SATURN, sky.at(jds))
    return np.isin((lons // 30).astype(int) % 12, span)


def sade_sati_windows(sky: Sky, moon_sign: int, start: datetime, end: datetime) -> list[SadeSati]:
    """Sade Sati periods overlapping [start, end] for a natal Moon in `moon_sign`."""
    span = [(moon_sign - 1) % 12, moon_sign, (moon_sign + 1) % 12]
    jd_start, jd_end = julian_day(start), julian_day(end)
    jd0 = max(jd_start - _MARGIN_DAYS, MIN_JD)
    jd1 = min(jd_end + _MARGIN_DAYS, MAX_JD)

    grid = np.arange(jd0, jd1, _SCAN_STEP_DAYS)
    states = _in_span(sky, grid, span)

    # Refine every coarse transition at hourly resolution, in one vectorised pass.
    flips = np.flatnonzero(states[1:] != states[:-1])
    fine = grid[flips][:, None] + _REFINE_OFFSETS[None, :]
    fine_states = _in_span(sky, fine.ravel(), span).reshape(fine.shape)

    windows: list[list[float]] = []
    open_at = jd0 if states[0] else None
    for row, i in enumerate(flips):
        entering = bool(states[i + 1])
        edge = float(fine[row, np.argmax(fine_states[row] == entering)])
        if entering:
            if windows and edge - windows[-1][1] < _MERGE_GAP_DAYS:
                open_at = windows.pop()[0]
            else:
                open_at = edge
        elif open_at is not None:
            windows.append([open_at, edge])
            open_at = None

    # A window still open at the scan end, or open from its start, has an unknown true
    # edge (only possible near the ephemeris limits); leave it out rather than guess.
    return [
        SadeSati(start=datetime_from_jd(a), end=datetime_from_jd(b))
        for a, b in windows
        if a > jd0 and b > jd_start and a < jd_end
    ]
