"""Vimshottari dasha from the Moon's nakshatra."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from guruji.astro.chart import nakshatra_index
from guruji.astro.constants import (
    DASHA_ORDER,
    DASHA_TOTAL_YEARS,
    DASHA_YEAR_DAYS,
    DASHA_YEARS,
    NAKSHATRA_SPAN,
    Graha,
)


class DashaPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)

    lord: Graha
    start: datetime
    end: datetime
    sub: list[DashaPeriod] = []

    def contains(self, when: datetime) -> bool:
        return self.start <= when < self.end


def birth_dasha(moon_longitude: float) -> tuple[Graha, float]:
    """Lord of the dasha running at birth and the fraction of it already elapsed."""
    nak = nakshatra_index(moon_longitude)
    elapsed = (moon_longitude % NAKSHATRA_SPAN) / NAKSHATRA_SPAN
    return DASHA_ORDER[nak % 9], elapsed


def balance_years(moon_longitude: float) -> float:
    """Years of the birth mahadasha still to run at birth."""
    lord, elapsed = birth_dasha(moon_longitude)
    return DASHA_YEARS[lord] * (1.0 - elapsed)


def _periods(first: Graha, start: datetime, total_days: float, levels: int) -> list[DashaPeriod]:
    """One full cycle of nine periods starting with `first`, sized in proportion to their years."""
    i0 = DASHA_ORDER.index(first)
    out: list[DashaPeriod] = []
    cum = 0.0
    for k in range(9):
        lord = DASHA_ORDER[(i0 + k) % 9]
        days = total_days * DASHA_YEARS[lord] / DASHA_TOTAL_YEARS
        p_start = start + timedelta(days=cum)
        cum += days
        p_end = start + timedelta(days=total_days) if k == 8 else start + timedelta(days=cum)
        sub = _periods(lord, p_start, days, levels - 1) if levels > 1 else []
        out.append(DashaPeriod(lord=lord, start=p_start, end=p_end, sub=sub))
    return out


def vimshottari(moon_longitude: float, birth: datetime, levels: int = 2) -> list[DashaPeriod]:
    """The nine mahadashas of the 120-year cycle that is running at birth.

    The first mahadasha starts before birth (its elapsed part); levels=2 adds antardashas,
    levels=3 pratyantardashas.
    """
    if not 1 <= levels <= 5:
        raise ValueError("levels must be between 1 and 5")
    lord, elapsed = birth_dasha(moon_longitude)
    start = birth - timedelta(days=DASHA_YEARS[lord] * DASHA_YEAR_DAYS * elapsed)
    return _periods(lord, start, DASHA_TOTAL_YEARS * DASHA_YEAR_DAYS, levels)


def dasha_at(periods: list[DashaPeriod], when: datetime) -> list[DashaPeriod]:
    """Chain of running periods at `when`, outermost first (e.g. [mahadasha, antardasha])."""
    chain: list[DashaPeriod] = []
    level = periods
    while level:
        running = next((p for p in level if p.contains(when)), None)
        if running is None:
            break
        chain.append(running)
        level = running.sub
    return chain


def subperiods(period: DashaPeriod, levels: int = 1) -> list[DashaPeriod]:
    """The next level down inside `period` (e.g. pratyantardashas of an antardasha)."""
    days = (period.end - period.start).total_seconds() / 86400
    return _periods(period.lord, period.start, days, levels)
