"""Vedic astrology engine. Deterministic maths only; the LLM interprets, never computes.

Pure: no network, database or file access. The ephemeris arrives as a loaded `Sky`
(guruji.ephemeris.load_sky). Swiss Ephemeris (AGPL) is a test oracle only and never imported here.
"""

from guruji.astro.dossier import ENGINE_VERSION, BirthInput, Dossier, compute_dossier
from guruji.astro.sky import OutOfRangeError, Sky
from guruji.astro.transits import TransitSnapshot, transit_snapshot

__all__ = [
    "ENGINE_VERSION",
    "BirthInput",
    "Dossier",
    "OutOfRangeError",
    "Sky",
    "TransitSnapshot",
    "compute_dossier",
    "transit_snapshot",
]
