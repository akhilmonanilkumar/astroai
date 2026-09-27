"""The chart dossier: everything the guru needs about a birth chart, computed once.

Stored as JSON in `charts.dossier`, keyed by ENGINE_VERSION. Bump the version whenever a
change would alter any stored value, so old dossiers can be recomputed.

The dossier holds no name, place or raw birth moment, but its positions and dasha dates
still identify the birth, so treat it as personal data (never log it).
"""

from datetime import UTC, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from guruji.astro.chart import (
    NavamsaChart,
    RashiChart,
    nakshatra_index,
    navamsa_chart,
    rashi_chart,
    sign_index,
)
from guruji.astro.constants import DASHA_YEAR_DAYS, Graha
from guruji.astro.dasha import DashaPeriod, balance_years, birth_dasha, vimshottari
from guruji.astro.panchang import Panchang, panchang
from guruji.astro.sky import (
    MAX_JD,
    Sky,
    ascendant,
    ayanamsa_true,
    datetime_from_jd,
    graha_states,
    julian_day,
    sidereal_longitude,
)
from guruji.astro.transits import SadeSati, sade_sati_windows
from guruji.astro.yogas import Yoga, find_yogas

ENGINE_VERSION = "astro-1"
_SADE_SATI_YEARS = 100


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class BirthInput(_Model):
    """Birth moment in its local timezone (see guruji.geo.tz.birth_moment)."""

    moment: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # Unknown birth time: pass local noon. No lagna; houses count from the Moon.
    time_known: bool = True

    @field_validator("moment")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("moment must be timezone-aware")
        return v


class EngineSettings(_Model):
    ayanamsa: Literal["lahiri"] = "lahiri"
    node: Literal["mean"] = "mean"
    houses: Literal["whole_sign"] = "whole_sign"
    dasha: Literal["vimshottari"] = "vimshottari"
    dasha_year_days: float = DASHA_YEAR_DAYS


class DashaSummary(_Model):
    birth_lord: Graha
    balance_years: float  # of the birth mahadasha, at birth
    mahadashas: list[DashaPeriod]  # UTC, with antardashas in `sub`


class Dossier(_Model):
    engine_version: str
    settings: EngineSettings
    computed_at: datetime
    time_known: bool
    # Facts that could differ within the birth day when the time is unknown,
    # e.g. ["moon_nakshatra"]; the guru should not lean on them.
    uncertain: list[str]
    ayanamsa: float
    d1: RashiChart
    d9: NavamsaChart
    panchang: Panchang
    dasha: DashaSummary
    yogas: list[Yoga]
    sade_sati: list[SadeSati]


def _uncertain_without_time(sky: Sky, birth: BirthInput) -> list[str]:
    tz = birth.moment.tzinfo
    day = birth.moment.date()
    first = datetime.combine(day, time(0, 0), tz)
    last = datetime.combine(day, time(23, 59, 59), tz)
    lo, hi = (
        float(sidereal_longitude(sky, Graha.MOON, sky.at(julian_day(t)))) for t in (first, last)
    )
    out = []
    if sign_index(lo) != sign_index(hi):
        out.append("moon_sign")
    if nakshatra_index(lo) != nakshatra_index(hi):
        out.append("moon_nakshatra")
    out.append("dasha_dates")  # the Moon moves ~13° a day, so dasha balance is approximate
    return out


def compute_dossier(sky: Sky, birth: BirthInput, computed_at: datetime) -> Dossier:
    jd = julian_day(birth.moment)
    states = graha_states(sky, jd)
    asc = ascendant(sky, jd, birth.latitude, birth.longitude) if birth.time_known else None
    d1 = rashi_chart(states, asc)
    moon = states[Graha.MOON].longitude
    sun = states[Graha.SUN].longitude
    assert birth.moment.tzinfo is not None  # checked by BirthInput

    lord, _ = birth_dasha(moon)
    sade_end = min(
        birth.moment + timedelta(days=_SADE_SATI_YEARS * 365.25), datetime_from_jd(MAX_JD)
    )
    return Dossier(
        engine_version=ENGINE_VERSION,
        settings=EngineSettings(),
        computed_at=computed_at,
        time_known=birth.time_known,
        uncertain=[] if birth.time_known else _uncertain_without_time(sky, birth),
        ayanamsa=round(float(ayanamsa_true(sky.at(jd))), 6),
        d1=d1,
        d9=navamsa_chart(d1),
        panchang=panchang(sky, jd, sun, moon, birth.latitude, birth.longitude, birth.moment.tzinfo),
        dasha=DashaSummary(
            birth_lord=lord,
            balance_years=round(balance_years(moon), 4),
            mahadashas=vimshottari(moon, birth.moment.astimezone(UTC), levels=2),
        ),
        yogas=find_yogas(d1),
        sade_sati=sade_sati_windows(sky, sign_index(moon), birth.moment, sade_end),
    )
