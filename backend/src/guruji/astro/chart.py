"""Rashi (D1) and navamsa (D9) charts from sidereal longitudes. Whole-sign houses."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from guruji.astro.constants import (
    COMBUSTION_ORB,
    DASHA_ORDER,
    DEBILITATION_SIGN,
    ENEMIES,
    EXALTATION_SIGN,
    FRIENDS,
    GRAHAS,
    MOOLATRIKONA,
    NAKSHATRA_SPAN,
    NAKSHATRAS,
    PADA_SPAN,
    SIGN_LORDS,
    SIGNS,
    Graha,
)
from guruji.astro.sky import GrahaState, signed_diff


class Dignity(StrEnum):
    EXALTED = "exalted"
    MOOLATRIKONA = "moolatrikona"
    OWN = "own"
    FRIEND = "friend"
    NEUTRAL = "neutral"
    ENEMY = "enemy"
    DEBILITATED = "debilitated"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


def sign_index(longitude: float) -> int:
    return int(longitude // 30) % 12


def nakshatra_index(longitude: float) -> int:
    return int(longitude // NAKSHATRA_SPAN) % 27


def house_from(sign: int, reference_sign: int) -> int:
    """Whole-sign house (1-12) of `sign` counted from `reference_sign`."""
    return (sign - reference_sign) % 12 + 1


def navamsa_sign(longitude: float) -> int:
    """D9 sign: each sign has nine 3°20' parts, counted on continuously from Aries."""
    return int(longitude * 9 // 30) % 12


class Placement(_Model):
    longitude: float
    sign: str
    degree: float  # within the sign
    nakshatra: str
    pada: int
    nakshatra_lord: Graha

    @property
    def sign_index(self) -> int:
        return sign_index(self.longitude)

    @property
    def nakshatra_index(self) -> int:
        return nakshatra_index(self.longitude)


def placement(longitude: float) -> Placement:
    lon = round(longitude % 360.0, 6) % 360.0
    nak = nakshatra_index(lon)
    return Placement(
        longitude=lon,
        sign=SIGNS[sign_index(lon)],
        degree=round(lon % 30, 6),
        nakshatra=NAKSHATRAS[nak],
        pada=int((lon % NAKSHATRA_SPAN) // PADA_SPAN) + 1,
        nakshatra_lord=DASHA_ORDER[nak % 9],
    )


class GrahaPlacement(Placement):
    graha: Graha
    speed: float  # degrees per day
    retrograde: bool
    house: int
    dignity: Dignity | None
    combust: bool


class RashiChart(_Model):
    """D1. `house_basis` is "moon" (Chandra lagna) when the birth time is unknown."""

    lagna: Placement | None
    house_basis: Literal["lagna", "moon"]
    grahas: list[GrahaPlacement]

    def graha(self, graha: Graha) -> GrahaPlacement:
        return next(p for p in self.grahas if p.graha == graha)

    @property
    def reference_sign(self) -> int:
        """Sign counted as the 1st house."""
        if self.lagna is not None:
            return self.lagna.sign_index
        return self.graha(Graha.MOON).sign_index

    def house_of(self, graha: Graha) -> int:
        return self.graha(graha).house

    def occupants(self, house: int) -> list[Graha]:
        return [p.graha for p in self.grahas if p.house == house]


class NavamsaChart(_Model):
    """D9: sign of the lagna and of each graha, plus vargottama grahas (same sign in D1 and D9)."""

    lagna_sign: str | None
    signs: dict[Graha, str]
    vargottama: list[Graha]


def dignity(graha: Graha, longitude: float) -> Dignity | None:
    if graha not in EXALTATION_SIGN:
        return None
    sign = sign_index(longitude)
    deg = longitude % 30
    # Whole-sign exaltation, as in popular Indian software: it outranks the moolatrikona
    # span the Moon (Taurus) and Mercury (Virgo) have in the same sign.
    if sign == EXALTATION_SIGN[graha]:
        return Dignity.EXALTED
    if sign == DEBILITATION_SIGN[graha]:
        return Dignity.DEBILITATED
    mt_sign, mt_from, mt_to = MOOLATRIKONA[graha]
    if sign == mt_sign and mt_from <= deg < mt_to:
        return Dignity.MOOLATRIKONA
    lord = SIGN_LORDS[sign]
    if lord is graha:
        return Dignity.OWN
    if lord in FRIENDS[graha]:
        return Dignity.FRIEND
    if lord in ENEMIES[graha]:
        return Dignity.ENEMY
    return Dignity.NEUTRAL


def is_combust(graha: Graha, longitude: float, sun_longitude: float, retrograde: bool) -> bool:
    if graha not in COMBUSTION_ORB:
        return False
    direct_orb, retro_orb = COMBUSTION_ORB[graha]
    orb = retro_orb if retrograde else direct_orb
    return abs(signed_diff(longitude, sun_longitude)) < orb


def rashi_chart(states: dict[Graha, GrahaState], ascendant: float | None) -> RashiChart:
    lagna = placement(ascendant) if ascendant is not None else None
    moon_sign = sign_index(states[Graha.MOON].longitude)
    reference = lagna.sign_index if lagna is not None else moon_sign
    sun = states[Graha.SUN].longitude
    grahas = []
    for g in GRAHAS:
        st = states[g]
        # Mean nodes always move backwards; for them "retrograde" is by definition.
        retro = st.speed < 0
        grahas.append(
            GrahaPlacement(
                **placement(st.longitude).model_dump(),
                graha=g,
                speed=round(st.speed, 6),
                retrograde=retro,
                house=house_from(sign_index(st.longitude), reference),
                dignity=dignity(g, st.longitude),
                combust=is_combust(g, st.longitude, sun, retro),
            )
        )
    return RashiChart(
        lagna=lagna, house_basis="lagna" if lagna is not None else "moon", grahas=grahas
    )


def navamsa_chart(d1: RashiChart) -> NavamsaChart:
    signs = {p.graha: SIGNS[navamsa_sign(p.longitude)] for p in d1.grahas}
    return NavamsaChart(
        lagna_sign=SIGNS[navamsa_sign(d1.lagna.longitude)] if d1.lagna else None,
        signs=signs,
        vargottama=[p.graha for p in d1.grahas if navamsa_sign(p.longitude) == p.sign_index],
    )
