"""Chart facts as compact plain text for the guru's prompt and tool results.

The chart block is stable per user (it only changes with ENGINE_VERSION), so it sits in
the cached part of the prompt; the "today" block changes every turn and comes last.
"""

from datetime import date, datetime

from guruji.astro import Dossier, TransitSnapshot
from guruji.astro.dasha import DashaPeriod, dasha_at, subperiods
from guruji.db.models import LifeFact, Reading
from guruji.rag.cards import Card


def dms(deg: float) -> str:
    d = int(deg)
    m = round((deg - d) * 60)
    if m == 60:
        d, m = d + 1, 0
    return f"{d}°{m:02d}'"


def _day(dt: datetime | date) -> str:
    return dt.strftime("%d %b %Y")


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def chart_block(d: Dossier) -> str:
    lines = ["BIRTH CHART (sidereal, Lahiri ayanamsa, whole-sign houses, mean nodes)"]
    if d.d1.lagna is not None:
        lg = d.d1.lagna
        lines.append(
            f"Lagna: {lg.sign} {dms(lg.degree)}, {lg.nakshatra} pada {lg.pada} "
            f"(nakshatra lord {lg.nakshatra_lord})"
        )
    else:
        lines.append(
            "Birth time unknown: no lagna. Houses below are counted from the Moon's sign "
            "(Chandra lagna)."
        )
    if d.uncertain:
        lines.append(
            "Uncertain because the birth time is unknown (do not rely on): "
            + ", ".join(u.replace("_", " ") for u in d.uncertain)
        )
    lines.append("Grahas: sign degree, nakshatra-pada, house, dignity, flags")
    for p in d.d1.grahas:
        flags = [
            f
            for f, on in (
                ("retrograde", p.retrograde and p.graha not in ("Rahu", "Ketu")),
                ("combust", p.combust),
            )
            if on
        ]
        dignity = f", {p.dignity}" if p.dignity else ""
        lines.append(
            f"- {p.graha}: {p.sign} {dms(p.degree)}, {p.nakshatra}-{p.pada}, "
            f"{_ordinal(p.house)} house{dignity}{', ' + ', '.join(flags) if flags else ''}"
        )
    d9 = ", ".join(f"{g} {s}" for g, s in d.d9.signs.items())
    lines.append(f"Navamsa (D9): lagna {d.d9.lagna_sign or 'n/a'}; {d9}")
    if d.d9.vargottama:
        lines.append("Vargottama: " + ", ".join(d.d9.vargottama))
    pc = d.panchang
    lines.append(
        f"Birth panchang: {pc.paksha} {pc.tithi}, {pc.nakshatra} nakshatra, {pc.yoga} yoga, "
        f"{pc.karana} karana, {pc.vara}"
    )
    if d.yogas:
        lines.append("Yogas and doshas (base rules; weigh cancellations yourself):")
        lines.extend(f"- {y.name} ({y.kind}): {y.detail}" for y in d.yogas)
    lines.append(
        f"Vimshottari: born in {d.dasha.birth_lord} mahadasha "
        f"({d.dasha.balance_years:.1f} years remaining at birth). Mahadashas:"
    )
    lines.extend(f"- {m.lord}: {_day(m.start)} to {_day(m.end)}" for m in d.dasha.mahadashas)
    if d.sade_sati:
        lines.append(
            "Sade Sati periods: "
            + "; ".join(f"{_day(s.start)} to {_day(s.end)}" for s in d.sade_sati)
        )
    return "\n".join(lines)


def dasha_now(d: Dossier, now: datetime) -> str:
    chain = dasha_at(d.dasha.mahadashas, now)
    if not chain:
        return "Current dasha: outside the computed 120-year cycle."
    md = chain[0]
    parts = [f"Current dasha: {md.lord} mahadasha ({_day(md.start)} to {_day(md.end)})"]
    if len(chain) > 1:
        ad = chain[1]
        pd = next((p for p in subperiods(ad) if p.contains(now)), None)
        parts.append(f"{ad.lord} antardasha ({_day(ad.start)} to {_day(ad.end)})")
        if pd:
            parts.append(f"{pd.lord} pratyantardasha ({_day(pd.start)} to {_day(pd.end)})")
        upcoming = [a for a in md.sub if a.start > now][:2]
        if upcoming:
            parts.append(
                "next: " + ", ".join(f"{a.lord} antardasha from {_day(a.start)}" for a in upcoming)
            )
    return "; ".join(parts)


def transits_block(t: TransitSnapshot, time_known: bool) -> str:
    lines = [f"Transits on {_day(t.at)} (house from natal lagna / from natal Moon):"]
    for p in t.grahas:
        lagna = (
            f"{_ordinal(p.house_from_lagna)} from lagna, "
            if time_known and p.house_from_lagna
            else ""
        )
        retro = ", retrograde" if p.retrograde and p.graha not in ("Rahu", "Ketu") else ""
        lines.append(
            f"- {p.graha}: {p.sign} {dms(p.longitude % 30)}, {lagna}"
            f"{_ordinal(p.house_from_moon)} from Moon{retro}"
        )
    if t.sade_sati_phase:
        lines.append(f"Sade Sati is running now ({t.sade_sati_phase} phase).")
    return "\n".join(lines)


def memory_block(facts: list[LifeFact], readings: list[Reading]) -> str:
    lines = ["WHAT YOU KNOW ABOUT THEIR LIFE"]
    lines.extend(f"- ({f.category}, {_day(f.created_at)}) {f.fact}" for f in facts)
    if not facts:
        lines.append("- nothing yet")
    lines.append("READINGS YOU HAVE GIVEN (stay consistent with these)")
    lines.extend(
        f"- {_day(r.created_at)} [{r.topic}] {r.summary}"
        + (f" (factors: {', '.join(r.factors)})" if r.factors else "")
        for r in readings
    )
    if not readings:
        lines.append("- none yet")
    return "\n".join(lines)


def periods_text(periods: list[DashaPeriod], label: str) -> str:
    return "\n".join(f"{label} {p.lord}: {_day(p.start)} to {_day(p.end)}" for p in periods)


def cards_block(cards: list[Card]) -> str:
    lines = [
        "RULE CARDS (traditional principles matching this chart; weigh them against the "
        "whole chart and interpret in your own words, never quote them)"
    ]
    lines.extend(f"- [{c.id}] {c.title}: {c.text}" for c in cards)
    return "\n".join(lines)
