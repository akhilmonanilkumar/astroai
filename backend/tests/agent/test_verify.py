"""The reply fact-checker: wrong claims are caught, right and vague ones pass."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest

from guruji.agent.verify import check_reply, feedback, strip_wrong
from guruji.astro import BirthInput, Dossier, Sky, TransitSnapshot, compute_dossier
from guruji.astro import transit_snapshot as snapshot
from guruji.astro.constants import GRAHAS, SIGNS, Graha
from guruji.astro.dasha import dasha_at
from guruji.config import Settings
from guruji.geo.places import PlaceIndex
from guruji.geo.tz import birth_moment

NOW = datetime(2026, 9, 28, 6, tzinfo=UTC)


@pytest.fixture(scope="module")
def chart(sky: Sky) -> tuple[Dossier, TransitSnapshot]:
    birth = BirthInput(
        moment=birth_moment(date(1990, 7, 15), time(9, 0), "Asia/Kolkata"),
        latitude=18.52,
        longitude=73.85,
        time_known=True,
    )
    d = compute_dossier(sky, birth, NOW)
    return d, snapshot(sky, d.d1, NOW)


def _check(text: str, chart: tuple[Dossier, TransitSnapshot], sky: Sky | None = None) -> list[str]:
    d, t = chart
    return [p.said for p in check_reply(text, d, NOW, sky=sky, transits=t)]


def _other(g: Graha, *avoid: Graha) -> Graha:
    return next(x for x in GRAHAS if x != g and x not in avoid)


def _natal(d: Dossier, g: Graha) -> tuple[int, str]:
    p = next(x for x in d.d1.grahas if x.graha == g)
    return p.house, p.sign


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# --- dashas -----------------------------------------------------------------------------


def test_running_antardasha(chart: tuple[Dossier, TransitSnapshot]) -> None:
    d, _ = chart
    md, ad = dasha_at(d.dasha.mahadashas, NOW)[:2]
    wrong = _other(ad.lord, md.lord)
    assert _check(f"Right now your {ad.lord} antardasha is running.", chart) == []
    assert _check(f"Abhi aapki {wrong} ki antardasha chal rahi hai.", chart)
    assert _check(f"अभी आपकी {md.lord} की महादशा चल रही है।", chart) == []


def test_the_reported_confusion_running_vs_next(chart: tuple[Dossier, TransitSnapshot]) -> None:
    """Saying the running antardasha starts next year (it started already) is caught."""
    d, _ = chart
    ad = dasha_at(d.dasha.mahadashas, NOW)[1]
    later = [a for m in d.dasha.mahadashas for a in m.sub if a.start > NOW]
    nxt = later[0]
    next_year = NOW.year + 1
    said = _check(f"Your {ad.lord} antardasha will start in April {next_year}.", chart)
    same_lord_soon = any(
        a.lord == ad.lord and abs(a.start - datetime(next_year, 4, 15, tzinfo=UTC)) < timedelta(60)
        for a in later
    )
    assert bool(said) != same_lord_soon
    ok = f"{nxt.lord} antardasha starts in {nxt.start:%B %Y}."
    assert _check(ok, chart) == []
    assert _check(f"Agli antardasha {nxt.lord} ki hogi.", chart) == []
    assert _check(f"Agli antardasha {_other(nxt.lord)} ki hogi.", chart)


def test_period_dates(chart: tuple[Dossier, TransitSnapshot]) -> None:
    d, _ = chart
    md, ad = dasha_at(d.dasha.mahadashas, NOW)[:2]
    right = f"{ad.lord} antardasha runs from {ad.start:%B %Y} to {ad.end:%B %Y}."
    assert _check(right, chart) == []
    assert _check(f"{md.lord} mahadasha {md.end:%B %Y} tak chalegi.", chart) == []
    off = ad.end + timedelta(days=400)
    assert _check(f"{ad.lord} antardasha ends in {off:%B %Y}.", chart)
    # Hindi, Devanagari months
    months = "जनवरी फरवरी मार्च अप्रैल मई जून जुलाई अगस्त सितंबर अक्टूबर नवंबर दिसंबर".split()
    assert (
        _check(f"{ad.lord} की अंतर्दशा {months[ad.end.month - 1]} {ad.end.year} तक है।", chart) == []
    )


# --- placements -------------------------------------------------------------------------


def test_natal_house_and_sign(chart: tuple[Dossier, TransitSnapshot]) -> None:
    d, _ = chart
    house, sign = _natal(d, Graha.SATURN)
    wrong_house = house % 12 + 1
    assert _check(f"Your Saturn sits in the {_ordinal(house)} house.", chart) == []
    assert _check(f"Your Saturn sits in the {_ordinal(wrong_house)} house.", chart)
    assert _check(f"Shani aapke {house}ve ghar mein hai.", chart) == []
    assert _check(f"Your Saturn is in {sign}.", chart) == []
    wrong_sign = SIGNS[(SIGNS.index(sign) + 3) % 12]
    assert _check(f"Your Saturn is in {wrong_sign}.", chart)


def test_lagna_and_rashi(chart: tuple[Dossier, TransitSnapshot]) -> None:
    d, _ = chart
    assert d.d1.lagna is not None
    lagna = d.d1.lagna.sign
    assert _check(f"Your lagna is {lagna}.", chart) == []
    assert _check(f"Your lagna is {SIGNS[(SIGNS.index(lagna) + 1) % 12]}.", chart)


def test_transits_now_and_dated(chart: tuple[Dossier, TransitSnapshot], sky: Sky) -> None:
    _, t = chart
    sat = t.graha(Graha.SATURN)
    assert _check(f"Saturn is transiting {sat.sign} now.", chart) == []
    wrong = SIGNS[(SIGNS.index(sat.sign) + 4) % 12]
    assert _check(f"Saturn is transiting {wrong} now.", chart)
    jup_later = snapshot(sky, chart[0].d1, NOW + timedelta(days=300)).graha(Graha.JUPITER).sign
    when = NOW + timedelta(days=300)
    assert _check(f"Jupiter enters {jup_later} around {when:%B %Y}.", chart, sky) == []
    far = SIGNS[(SIGNS.index(jup_later) + 6) % 12]
    assert _check(f"Jupiter enters {far} in {when:%B %Y}.", chart, sky)


def test_sade_sati(chart: tuple[Dossier, TransitSnapshot]) -> None:
    _, t = chart
    said = _check("Sade Sati is running for you right now.", chart)
    assert bool(said) == (t.sade_sati_phase is None)


# --- no false alarms -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Namaste ji, Guruji aapke saath hai. Sun lijiye, sab theek hoga.",
        "People with Saturn in the 7th often marry a little later.",
        "Saturn is not in your 7th house, so that worry does not apply.",
        "Jupiter aspects your 7th house from where it sits.",
        "The lord of your 7th house is placed in the 10th house.",
        "In your navamsa, Venus is in Pisces.",
        "Agar Shani 7ve ghar mein hota to deri hoti.",
        "Kanya ki shaadi ke baare mein poochh rahe hain?",
    ],
)
def test_ambiguous_or_general_lines_pass(text: str, chart: tuple[Dossier, TransitSnapshot]) -> None:
    assert _check(text, chart) == []


def test_feedback_and_strip(chart: tuple[Dossier, TransitSnapshot]) -> None:
    d, t = chart
    ad = dasha_at(d.dasha.mahadashas, NOW)[1]
    wrong = _other(ad.lord, dasha_at(d.dasha.mahadashas, NOW)[0].lord)
    text = f"Aapka samay achha hai.\n\nAbhi {wrong} ki antardasha chal rahi hai. Dhairya rakhiye."
    problems = check_reply(text, d, NOW, transits=t)
    assert len(problems) == 1
    note = feedback(problems)
    assert f"{ad.lord} antardasha" in note and "Rewrite" in note
    assert strip_wrong(text, problems) == "Aapka samay achha hai.\n\nDhairya rakhiye."


# --- through the turn graph ------------------------------------------------------------


async def test_wrong_timing_is_rewritten_then_dropped(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    from langchain_core.messages import AIMessage

    from .test_conversation import FIRST_READING, Chat, _onboard, _responder
    from .test_conversation import NOW as CHAT_NOW

    box: dict[str, Any] = {}

    def facts() -> tuple[Graha, Graha]:
        [raw] = box["store"].charts.values()
        md, ad = dasha_at(Dossier.model_validate(raw).dasha.mahadashas, CHAT_NOW)[:2]
        return ad.lord, _other(ad.lord, md.lord)

    def script() -> Iterator[AIMessage]:
        yield from FIRST_READING
        right, wrong = facts()
        # 1st question: wrong, then fixed on the first rewrite
        yield AIMessage(f"Abhi {wrong} ki antardasha chal rahi hai.")
        yield AIMessage(f"Abhi {right} ki antardasha chal rahi hai.")
        # 2nd question: stays wrong through both rewrites; the wrong sentence is dropped
        for _ in range(3):
            yield AIMessage(f"Dhairya rakhiye.\n\nAbhi {wrong} ki antardasha chal rahi hai.")

    responder, store, model = _responder(settings, sky, places, [])
    model.messages = script()
    box["store"] = store
    chat = Chat(responder)
    await _onboard(chat)
    right, _ = facts()

    r = await chat.send("abhi kaunsi dasha chal rahi hai?")
    assert r.bubbles == [f"Abhi {right} ki antardasha chal rahi hai."]
    fixes = [m for m in model.seen[-1] if "[fact check]" in str(m.content)]
    assert fixes and f"{right} antardasha" in str(fixes[0].content)

    r = await chat.send("pakka?")
    assert r.bubbles == ["Dhairya rakhiye."]
