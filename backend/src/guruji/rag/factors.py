"""Chart factors as lookup keys: the join between a user's chart and the rule cards.

Keys are lowercase and colon-separated, e.g. "saturn:house-7", "moon-nakshatra:revati",
"mahadasha:venus", "yoga:gajakesari", "transit:saturn-from-moon-12". Cards list the keys
they speak to; a chart yields its own set, and retrieval only looks at cards whose keys
the chart actually has.
"""

import re
from datetime import datetime

from guruji.astro import Dossier, TransitSnapshot
from guruji.astro.chart import Dignity
from guruji.astro.constants import GRAHAS, NAKSHATRAS, SIGNS
from guruji.astro.dasha import dasha_at
from guruji.astro.yogas import Yoga

_STRONG_OR_WEAK = {Dignity.EXALTED: "exalted", Dignity.DEBILITATED: "debilitated"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")


def yoga_key(y: Yoga) -> str:
    return f"yoga:{slug(y.name)}"


def natal_factors(d: Dossier) -> set[str]:
    keys: set[str] = set()
    if d.d1.lagna is not None:
        keys.add(f"lagna:{slug(d.d1.lagna.sign)}")
    for p in d.d1.grahas:
        g = slug(p.graha)
        # Houses count from the Moon when the time is unknown: still meaningful, but the
        # lagna-based house cards are what the corpus describes, so only use real houses.
        if d.d1.lagna is not None:
            keys.add(f"{g}:house-{p.house}")
        if p.dignity in _STRONG_OR_WEAK:
            keys.add(f"{g}:{_STRONG_OR_WEAK[p.dignity]}")
        if p.retrograde and p.graha not in ("Rahu", "Ketu"):
            keys.add(f"{g}:retrograde")
        if p.combust:
            keys.add(f"{g}:combust")
    moon = d.d1.graha("Moon")  # type: ignore[arg-type]
    if "moon_sign" not in d.uncertain:
        keys.add(f"moon:{slug(moon.sign)}")
    if "moon_nakshatra" not in d.uncertain:
        keys.add(f"moon-nakshatra:{slug(moon.nakshatra)}")
    keys |= {yoga_key(y) for y in d.yogas}
    return keys


def current_factors(d: Dossier, now: datetime, transits: TransitSnapshot | None) -> set[str]:
    keys: set[str] = set()
    chain = dasha_at(d.dasha.mahadashas, now)
    if chain:
        keys.add(f"mahadasha:{slug(chain[0].lord)}")
    if len(chain) > 1:
        keys.add(f"antardasha:{slug(chain[1].lord)}")
    if transits is not None:
        for p in transits.grahas:
            if p.graha in ("Saturn", "Jupiter", "Rahu"):
                keys.add(f"transit:{slug(p.graha)}-from-moon-{p.house_from_moon}")
        if transits.sade_sati_phase:
            keys.add(f"sade-sati:{transits.sade_sati_phase}")
    return keys


def known_keys() -> set[str]:
    """Every key the charts can produce: cards may only use these."""
    graha_slugs = [slug(g) for g in GRAHAS]
    keys = {f"lagna:{slug(s)}" for s in SIGNS} | {f"moon:{slug(s)}" for s in SIGNS}
    keys |= {f"moon-nakshatra:{slug(n)}" for n in NAKSHATRAS}
    for g in graha_slugs:
        keys |= {f"{g}:house-{h}" for h in range(1, 13)}
        keys |= {f"{g}:exalted", f"{g}:debilitated", f"{g}:retrograde", f"{g}:combust"}
        keys |= {f"mahadasha:{g}", f"antardasha:{g}"}
    for g in ("saturn", "jupiter", "rahu"):
        keys |= {f"transit:{g}-from-moon-{h}" for h in range(1, 13)}
    keys |= {f"sade-sati:{p}" for p in ("rising", "peak", "setting")}
    keys |= {
        f"yoga:{slug(n)}"
        for n in (
            "Gajakesari Budhaditya Chandra-Mangala Ruchaka Bhadra Hamsa Malavya Sasa Sunapha "
            "Anapha Durdhara Kemadruma Manglik Kaal-Sarp Guru-Chandal Grahan"
        ).split()
    }
    return keys
