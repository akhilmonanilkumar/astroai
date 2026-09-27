"""Panchang at a moment: tithi, nakshatra, nitya yoga, karana and vara."""

from datetime import tzinfo
from typing import Literal

from pydantic import BaseModel, ConfigDict

from guruji.astro.chart import nakshatra_index
from guruji.astro.constants import (
    MOVABLE_KARANAS,
    NAKSHATRA_SPAN,
    NAKSHATRAS,
    NITYA_YOGAS,
    TITHIS,
    WEEKDAY_LORDS,
    WEEKDAYS,
    Graha,
)
from guruji.astro.sky import Sky, datetime_from_jd, previous_sunrise


class Panchang(BaseModel):
    model_config = ConfigDict(frozen=True)

    tithi: str
    tithi_number: int  # 1-30, counted from Shukla Pratipada
    paksha: Literal["Shukla", "Krishna"]
    nakshatra: str
    yoga: str
    karana: str
    vara: str
    vara_lord: Graha


def tithi(sun: float, moon: float) -> tuple[int, str, Literal["Shukla", "Krishna"]]:
    n = int(((moon - sun) % 360.0) // 12) + 1
    paksha: Literal["Shukla", "Krishna"] = "Shukla" if n <= 15 else "Krishna"
    if n == 15:
        return n, "Purnima", paksha
    if n == 30:
        return n, "Amavasya", paksha
    return n, TITHIS[(n - 1) % 15], paksha


def nitya_yoga(sun: float, moon: float) -> str:
    return NITYA_YOGAS[int(((sun + moon) % 360.0) // NAKSHATRA_SPAN) % 27]


def karana(sun: float, moon: float) -> str:
    k = int(((moon - sun) % 360.0) // 6)  # half-tithi, 0-59
    fixed = {0: "Kimstughna", 57: "Shakuni", 58: "Chatushpada", 59: "Naga"}
    return fixed.get(k) or MOVABLE_KARANAS[(k - 1) % 7]


def vara_index(sky: Sky, jd_ut: float, latitude: float, longitude: float, tz: tzinfo) -> int:
    """Python weekday (Mon=0) of the Vedic day, which begins at local sunrise."""
    sunrise = previous_sunrise(sky, jd_ut, latitude, longitude)
    anchor = datetime_from_jd(sunrise if sunrise is not None else jd_ut)
    return anchor.astimezone(tz).weekday()


def panchang(
    sky: Sky,
    jd_ut: float,
    sun: float,
    moon: float,
    latitude: float,
    longitude: float,
    tz: tzinfo,
) -> Panchang:
    n, name, paksha = tithi(sun, moon)
    wd = vara_index(sky, jd_ut, latitude, longitude, tz)
    return Panchang(
        tithi=name,
        tithi_number=n,
        paksha=paksha,
        nakshatra=NAKSHATRAS[nakshatra_index(moon)],
        yoga=nitya_yoga(sun, moon),
        karana=karana(sun, moon),
        vara=WEEKDAYS[wd],
        vara_lord=WEEKDAY_LORDS[wd],
    )
