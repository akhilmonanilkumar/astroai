from guruji.astro.chart import RashiChart, rashi_chart
from guruji.astro.constants import Graha
from guruji.astro.sky import GrahaState

# A neutral spread: one graha per sign, nothing notable (overridden per test).
BASE: dict[Graha, float] = {
    Graha.SUN: 45.0,  # Taurus
    Graha.MOON: 105.0,  # Cancer
    Graha.MARS: 195.0,  # Libra
    Graha.MERCURY: 55.0,  # Taurus
    Graha.JUPITER: 315.0,  # Aquarius (8th from the Moon: no Gajakesari)
    Graha.VENUS: 75.0,  # Gemini
    Graha.SATURN: 165.0,  # Virgo
    Graha.RAHU: 10.0,  # Aries
}


def chart(ascendant: float | None = 5.0, **lons: float) -> RashiChart:
    """Rashi chart from longitudes; keyword names are graha values (Sun=..., Rahu=...)."""
    pos = dict(BASE)
    pos.update({Graha(k): v for k, v in lons.items()})
    pos[Graha.KETU] = (pos[Graha.RAHU] + 180.0) % 360.0
    states = {
        g: GrahaState(lon, -0.053 if g in (Graha.RAHU, Graha.KETU) else 1.0)
        for g, lon in pos.items()
    }
    return rashi_chart(states, ascendant)
