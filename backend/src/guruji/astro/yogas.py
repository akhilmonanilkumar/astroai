"""Yogas and doshas detected from the rashi chart.

Only well-defined, commonly asked combinations, using their base (unqualified) rules.
Each hit states the placements that formed it; weighing cancellations and strength is
interpretation, left to the guru agent and the RAG rule cards.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from guruji.astro.chart import Dignity, RashiChart, house_from
from guruji.astro.constants import KENDRAS, SIGNS, Graha
from guruji.astro.sky import signed_diff


class Yoga(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    kind: Literal["yoga", "dosha"]
    grahas: list[Graha]
    detail: str


_MAHAPURUSHA: dict[Graha, str] = {
    Graha.MARS: "Ruchaka",
    Graha.MERCURY: "Bhadra",
    Graha.JUPITER: "Hamsa",
    Graha.VENUS: "Malavya",
    Graha.SATURN: "Sasa",
}
_STRONG = {Dignity.EXALTED, Dignity.MOOLATRIKONA, Dignity.OWN}
# Grahas counted around the Moon for Sunapha / Anapha / Durdhara / Kemadruma.
_TARA_GRAHAS = (Graha.MARS, Graha.MERCURY, Graha.JUPITER, Graha.VENUS, Graha.SATURN)
_MANGLIK_HOUSES = frozenset({1, 2, 4, 7, 8, 12})
_NODES = (Graha.RAHU, Graha.KETU)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _names(grahas: list[Graha]) -> str:
    return ", ".join(g.value for g in grahas)


def find_yogas(d1: RashiChart) -> list[Yoga]:
    out: list[Yoga] = []
    sign = {p.graha: p.sign_index for p in d1.grahas}
    moon = sign[Graha.MOON]

    def from_moon(g: Graha) -> int:
        return house_from(sign[g], moon)

    jup = from_moon(Graha.JUPITER)
    if jup in KENDRAS:
        out.append(
            Yoga(
                name="Gajakesari",
                kind="yoga",
                grahas=[Graha.JUPITER, Graha.MOON],
                detail=f"Jupiter in the {_ordinal(jup)} house from the Moon",
            )
        )

    if sign[Graha.SUN] == sign[Graha.MERCURY]:
        combust = " (Mercury combust)" if d1.graha(Graha.MERCURY).combust else ""
        out.append(
            Yoga(
                name="Budhaditya",
                kind="yoga",
                grahas=[Graha.SUN, Graha.MERCURY],
                detail=f"Sun and Mercury together in {SIGNS[sign[Graha.SUN]]}{combust}",
            )
        )

    if sign[Graha.MOON] == sign[Graha.MARS]:
        out.append(
            Yoga(
                name="Chandra-Mangala",
                kind="yoga",
                grahas=[Graha.MOON, Graha.MARS],
                detail=f"Moon and Mars together in {SIGNS[moon]}",
            )
        )

    if d1.lagna is not None:
        for g, yoga_name in _MAHAPURUSHA.items():
            p = d1.graha(g)
            if p.dignity in _STRONG and p.house in KENDRAS:
                out.append(
                    Yoga(
                        name=yoga_name,
                        kind="yoga",
                        grahas=[g],
                        detail=f"{g.value} {p.dignity} in {p.sign}, "
                        f"{_ordinal(p.house)} house from the lagna",
                    )
                )

    second = [g for g in _TARA_GRAHAS if from_moon(g) == 2]
    twelfth = [g for g in _TARA_GRAHAS if from_moon(g) == 12]
    if second and twelfth:
        out.append(
            Yoga(
                name="Durdhara",
                kind="yoga",
                grahas=[*second, *twelfth],
                detail=f"{_names(second)} in the 2nd and {_names(twelfth)} in the 12th "
                "from the Moon",
            )
        )
    elif second:
        out.append(
            Yoga(
                name="Sunapha",
                kind="yoga",
                grahas=second,
                detail=f"{_names(second)} in the 2nd from the Moon",
            )
        )
    elif twelfth:
        out.append(
            Yoga(
                name="Anapha",
                kind="yoga",
                grahas=twelfth,
                detail=f"{_names(twelfth)} in the 12th from the Moon",
            )
        )
    else:
        out.append(
            Yoga(
                name="Kemadruma",
                kind="dosha",
                grahas=[Graha.MOON],
                detail="no graha other than the Sun and nodes in the 2nd or 12th from the Moon",
            )
        )

    mars_from_moon = from_moon(Graha.MARS)
    bases = []
    if d1.lagna is not None and d1.house_of(Graha.MARS) in _MANGLIK_HOUSES:
        bases.append(f"{_ordinal(d1.house_of(Graha.MARS))} house from the lagna")
    if mars_from_moon in _MANGLIK_HOUSES:
        bases.append(f"{_ordinal(mars_from_moon)} house from the Moon")
    if bases:
        out.append(
            Yoga(
                name="Manglik",
                kind="dosha",
                grahas=[Graha.MARS],
                detail="Mars in the " + " and the ".join(bases),
            )
        )

    rahu = d1.graha(Graha.RAHU).longitude
    sides = {signed_diff(d1.graha(g).longitude, rahu) > 0 for g in Graha if g not in _NODES}
    if len(sides) == 1:
        side = "from Rahu to Ketu" if sides == {True} else "from Ketu to Rahu"
        out.append(
            Yoga(
                name="Kaal Sarp",
                kind="dosha",
                grahas=[Graha.RAHU, Graha.KETU],
                detail=f"all seven grahas lie on one side of the nodal axis, {side}",
            )
        )

    if sign[Graha.JUPITER] in (sign[Graha.RAHU], sign[Graha.KETU]):
        node = Graha.RAHU if sign[Graha.JUPITER] == sign[Graha.RAHU] else Graha.KETU
        out.append(
            Yoga(
                name="Guru Chandal",
                kind="dosha",
                grahas=[Graha.JUPITER, node],
                detail=f"Jupiter with {node.value} in {SIGNS[sign[Graha.JUPITER]]}",
            )
        )

    for luminary in (Graha.SUN, Graha.MOON):
        for node in _NODES:
            if sign[luminary] == sign[node]:
                out.append(
                    Yoga(
                        name="Grahan",
                        kind="dosha",
                        grahas=[luminary, node],
                        detail=f"{luminary.value} with {node.value} in {SIGNS[sign[luminary]]}",
                    )
                )
    return out
