"""Fact-check a guru reply against the chart before it is sent.

The model is given every date and placement it needs, and still sometimes says the wrong
thing (e.g. that the Jupiter antardasha "starts next April" while it is running now). This
module finds concrete, checkable claims in a reply, in English, Hinglish and Hindi, and
compares each with the dossier and the ephemeris:

- dasha periods: which mahadasha / antardasha / pratyantardasha runs now, which is next,
  and when a period starts, ends or runs;
- natal placements: a graha's house or sign, the lagna sign, the Moon sign;
- transits: where a graha is now, or which sign it enters on a date;
- Sade Sati running now.

It is deliberately conservative: a sentence is only judged when it is unambiguous (one
graha per claim, no negation, no lordship, aspects or "from the Moon" counting, no
general "people with ..." talk). A false alarm costs a rewrite of a good answer; a
missed claim is still caught by the evals.

Pure: no I/O. The sky is only used for dated transit claims.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from guruji.astro import Dossier, OutOfRangeError, Sky, TransitSnapshot, transit_snapshot
from guruji.astro.constants import Graha
from guruji.astro.dasha import DashaPeriod, dasha_at, subperiods

# How far a stated month or year may sit from the true date and still count as right.
SLACK = timedelta(days=31)
_MAX_TRANSIT_SAMPLES = 16


@dataclass(frozen=True)
class Problem:
    """One wrong claim: the sentence that made it and the true fact."""

    sentence: str
    said: str
    fact: str


# --- vocabulary --------------------------------------------------------------------

_GRAHA_WORDS: dict[Graha, tuple[str, ...]] = {
    Graha.SUN: ("surya", "suraj", "सूर्य", "सूरज"),
    Graha.MOON: ("moon", "chandra", "chandrama", "चंद्र", "चन्द्र", "चंद्रमा", "चन्द्रमा"),
    Graha.MARS: ("mars", "mangal", "मंगल"),
    Graha.MERCURY: ("mercury", "budh", "budha", "बुध"),
    Graha.JUPITER: ("jupiter", "guru", "brihaspati", "brahaspati", "गुरु", "बृहस्पति"),
    Graha.VENUS: ("venus", "shukra", "शुक्र"),
    Graha.SATURN: ("saturn", "shani", "शनि"),
    Graha.RAHU: ("rahu", "राहु"),
    Graha.KETU: ("ketu", "केतु"),
}
# "sun" is also Hinglish for "listen": only the capitalised English word counts.
_SUN_EN = re.compile(r"\bSun\b")

_SIGN_WORDS: dict[str, tuple[str, ...]] = {
    "Aries": ("aries", "mesh", "mesha", "मेष"),
    "Taurus": ("taurus", "vrishabh", "vrishabha", "vrish", "vrishab", "वृषभ", "वृष"),
    "Gemini": ("gemini", "mithun", "mithuna", "मिथुन"),
    "Cancer": ("cancer", "kark", "karka", "कर्क"),
    "Leo": ("leo", "simha", "सिंह"),
    "Virgo": ("virgo",),  # kanya / कन्या also mean "girl": only with rashi, see below
    "Libra": ("libra", "tula", "तुला"),
    "Scorpio": ("scorpio", "vrishchik", "vrishchika", "vrischik", "वृश्चिक"),
    "Sagittarius": ("sagittarius", "dhanu", "dhanus", "धनु"),
    "Capricorn": ("capricorn", "makar", "makara", "मकर"),
    "Aquarius": ("aquarius", "kumbh", "kumbha", "कुंभ", "कुम्भ"),
    "Pisces": ("pisces", "meen", "meena", "मीन"),
}
_RASHI_ONLY = {"Virgo": ("kanya", "कन्या"), "Leo": ("singh",)}

_ORDINAL_WORDS: dict[int, tuple[str, ...]] = {
    1: ("first", "pehle", "pahle", "pratham", "पहले", "प्रथम"),
    2: ("second", "doosre", "dusre", "doosare", "dvitiya", "दूसरे", "द्वितीय"),
    3: ("third", "teesre", "tisre", "tritiya", "तीसरे", "तृतीय"),
    4: ("fourth", "chauthe", "chothe", "chaturth", "चौथे", "चतुर्थ"),
    5: ("fifth", "paanchve", "panchve", "paanchven", "panchven", "pancham", "पांचवें", "पाँचवें", "पंचम"),
    6: ("sixth", "chhathe", "chhate", "chathe", "shashth", "छठे", "षष्ठ"),
    7: ("seventh", "saatve", "saatven", "satve", "satven", "saptam", "सातवें", "सप्तम"),
    8: ("eighth", "aathve", "aathven", "athve", "ashtam", "आठवें", "अष्टम"),
    9: ("ninth", "nauve", "nauven", "nave", "naven", "navam", "नौवें", "नवें", "नवम"),
    10: ("tenth", "dasve", "dasven", "dasam", "dasham", "दसवें", "दशम"),
    11: ("eleventh", "gyarahve", "gyarahven", "gyarve", "ekadash", "ग्यारहवें", "एकादश"),
    12: ("twelfth", "barahve", "barahven", "barve", "dwadash", "बारहवें", "द्वादश"),
}
_HOUSE_NOUN = r"(?:house|bhav|bhaav|bhava|ghar|sthan|sthaan|भाव|घर|स्थान)"

_MONTHS: dict[int, tuple[str, ...]] = {
    1: ("january", "jan", "janvari", "janwari", "जनवरी"),
    2: ("february", "feb", "farvari", "farwari", "फरवरी", "फ़रवरी"),
    3: ("march", "mar", "मार्च"),
    4: ("april", "apr", "अप्रैल"),
    5: ("may", "mai", "मई"),
    6: ("june", "jun", "joon", "जून"),
    7: ("july", "jul", "julai", "जुलाई"),
    8: ("august", "aug", "agast", "अगस्त"),
    9: ("september", "sept", "sep", "sitambar", "सितंबर", "सितम्बर"),
    10: ("october", "oct", "aktubar", "aktoobar", "अक्टूबर", "अक्तूबर"),
    11: ("november", "nov", "navambar", "नवंबर", "नवम्बर"),
    12: ("december", "dec", "disambar", "दिसंबर", "दिसम्बर"),
}


def _alt(words: tuple[str, ...] | list[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


# Latin words need \b; Devanagari words end in combining marks, which \b mishandles, so
# they are bounded by "not a letter or mark" on either side instead.
_B0 = r"(?<![\wऀ-ॿ])"
_B1 = r"(?![\wऀ-ॿ])"


def _words(words: tuple[str, ...] | list[str]) -> str:
    return _B0 + "(?:" + _alt(words) + ")" + _B1


_GRAHA_RE = {g: re.compile(_words(w), re.IGNORECASE) for g, w in _GRAHA_WORDS.items()}
# "Guruji" is the persona's own name, not Jupiter.
_GURU_JI = re.compile(r"guru\s*ji|गुरुजी|गुरु\s*जी", re.IGNORECASE)

_SIGN_RE = {s: re.compile(_words(w), re.IGNORECASE) for s, w in _SIGN_WORDS.items()}
_RASHI_RE = {
    s: re.compile(_words(w) + r"\s*(?:rashi|raashi|राशि|lagna|लग्न)", re.IGNORECASE)
    for s, w in _RASHI_ONLY.items()
}
_HOUSE_NUM = re.compile(
    r"(?<!\d)(1[0-2]|[1-9])\s*(?:st|nd|rd|th|ve|ven|vein|vi|वें|वे|वां|वा)?\s*" + _HOUSE_NOUN + _B1,
    re.IGNORECASE,
)
_HOUSE_WORD = {
    n: re.compile(_words(w) + r"\s*" + _HOUSE_NOUN + _B1, re.IGNORECASE)
    for n, w in _ORDINAL_WORDS.items()
}

_MD = re.compile(r"maha\s*-?\s*dash[aā]|mahadasa|महादशा", re.IGNORECASE)
_PD = re.compile(r"pratyantar\s*-?\s*(?:dash[aā])?|प्रत्यंतर|प्रत्यन्तर", re.IGNORECASE)
_AD = re.compile(
    r"antar\s*-?\s*dash[aā]|antardasa|bhukti|" + _B0 + r"antar" + _B1 + r"|अंतर्दशा|अन्तर्दशा|अंतरदशा",
    re.IGNORECASE,
)
_BARE_DASHA = re.compile(_B0 + r"(?:dash[aā]|दशा)" + _B1, re.IGNORECASE)

_NOW = re.compile(
    _words(
        [
            "now",
            "currently",
            "current",
            "running",
            "ongoing",
            "at present",
            "presently",
            "these days",
            "chal rahi",
            "chal raha",
            "chal rahe",
            "chalti",
            "abhi",
            "aajkal",
            "is samay",
            "vartamaan",
            "vartaman",
            "अभी",
            "आजकल",
            "इस समय",
            "वर्तमान",
            "चल रही",
            "चल रहा",
            "चल रहे",
        ]
    ),
    re.IGNORECASE,
)
_NEXT = re.compile(
    _words(
        [
            "next",
            "upcoming",
            "agli",
            "agla",
            "agle",
            "aane wali",
            "aane wala",
            "अगली",
            "अगला",
            "अगले",
            "आने वाली",
            "आने वाला",
        ]
    ),
    re.IGNORECASE,
)
_START = re.compile(
    _words(
        [
            "start",
            "starts",
            "started",
            "starting",
            "begin",
            "begins",
            "began",
            "beginning",
            "from",
            "onwards",
            "commences",
            "kicks in",
            "shuru",
            "shuroo",
            "aarambh",
            "arambh",
            "se",
            "शुरू",
            "आरंभ",
            "प्रारंभ",
            "से",
        ]
    ),
    re.IGNORECASE,
)
_END = re.compile(
    _words(
        [
            "end",
            "ends",
            "ended",
            "ending",
            "till",
            "until",
            "up to",
            "upto",
            "finish",
            "finishes",
            "over by",
            "tak",
            "khatam",
            "samapt",
            "samaapt",
            "तक",
            "खत्म",
            "ख़त्म",
            "समाप्त",
        ]
    ),
    re.IGNORECASE,
)
_TRANSIT = re.compile(
    _words(
        [
            "transit",
            "transits",
            "transiting",
            "gochar",
            "गोचर",
            "moving through",
            "passing through",
        ]
    ),
    re.IGNORECASE,
)
_ENTER = re.compile(
    _words(
        [
            "enter",
            "enters",
            "entering",
            "entered",
            "moves into",
            "move into",
            "moving into",
            "moves to",
            "ingress",
            "pravesh",
            "प्रवेश",
            "aayega",
            "aayenge",
            "jayega",
            "jayenge",
            "आएगा",
            "आएंगे",
            "जाएगा",
            "जाएंगे",
        ]
    ),
    re.IGNORECASE,
)
_SKIP = re.compile(
    _words(
        [
            # negation
            "not",
            "no longer",
            "never",
            "isn't",
            "wasn't",
            "won't",
            "nahi",
            "nahin",
            "नहीं",
            "न",
            # lordship, aspects, other reference points, other charts
            "lord",
            "lords",
            "ruler",
            "rules",
            "ruled",
            "rulership",
            "owns",
            "swami",
            "svami",
            "adhipati",
            "malik",
            "स्वामी",
            "अधिपति",
            "aspect",
            "aspects",
            "aspecting",
            "drishti",
            "दृष्टि",
            "navamsa",
            "navamsha",
            "navansh",
            "d9",
            "नवांश",
            "from the moon",
            "from moon",
            "from your moon",
            "chandra se",
            "चंद्र से",
            # general statements and hypotheticals
            "people with",
            "those with",
            "jinki",
            "jin logon",
            "jiski",
            "generally",
            "usually",
            "if",
            "agar",
            "अगर",
            "जिनकी",
            "जिसकी",
        ]
    )
    + r"|n't\b",
    re.IGNORECASE,
)
_SADE_SATI = re.compile(r"sade\s*-?\s*sati|sadesati|साढ़े\s*साती|साढ़ेसाती", re.IGNORECASE)
_LAGNA = re.compile(_words(["lagna", "ascendant", "rising sign", "लग्न"]), re.IGNORECASE)
_RASHI = re.compile(
    _words(["moon sign", "rashi", "raashi", "chandra rashi", "राशि"]), re.IGNORECASE
)
_YOUR = re.compile(
    _words(["your", "aapka", "aapki", "aapke", "tumhara", "आपका", "आपकी", "आपके"]), re.IGNORECASE
)

_MONTH_RE = {m: _words(w) for m, w in _MONTHS.items()}
_ANY_MONTH = "(?:" + "|".join(_MONTH_RE.values()) + ")"
_YEAR = r"(?<!\d)(1[89]\d\d|20\d\d|21[0-4]\d)(?!\d)"
_MONTH_YEAR = re.compile(
    r"(" + _ANY_MONTH + r")[\s,]*(?:\d{1,2}(?:st|nd|rd|th)?,?\s*)?" + _YEAR, re.IGNORECASE
)
_DAY_MONTH_YEAR = re.compile(r"\d{1,2}\s*(" + _ANY_MONTH + r")[\s,]*" + _YEAR, re.IGNORECASE)
_NEXT_MONTH = re.compile(
    _words(["next", "agle", "agli", "अगले", "अगली"]) + r"\s*(" + _ANY_MONTH + ")", re.IGNORECASE
)
_THIS_YEAR = re.compile(
    _words(["this year", "is saal", "is varsh", "इस साल", "इस वर्ष"]), re.IGNORECASE
)
_NEXT_YEAR = re.compile(
    _words(["next year", "agle saal", "agle varsh", "अगले साल", "अगले वर्ष"]), re.IGNORECASE
)
_BARE_YEAR = re.compile(_YEAR)
_SENTENCES = re.compile(r"(?<=[.!?।])\s+|\n+")


# --- date mentions -------------------------------------------------------------------


@dataclass(frozen=True)
class _When:
    lo: datetime
    hi: datetime
    pos: int


def _month_of(token: str) -> int:
    t = token.lower()
    for m, words in _MONTHS.items():
        if t in (w.lower() for w in words):
            return m
    raise ValueError(token)


def _month_span(year: int, month: int, pos: int) -> _When:
    last = calendar.monthrange(year, month)[1]
    return _When(
        datetime(year, month, 1, tzinfo=UTC), datetime(year, month, last, 23, 59, tzinfo=UTC), pos
    )


def _year_span(year: int, pos: int) -> _When:
    return _When(datetime(year, 1, 1, tzinfo=UTC), datetime(year, 12, 31, 23, 59, tzinfo=UTC), pos)


def _dates(text: str, now: datetime) -> list[_When]:
    found: list[_When] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    for rx in (_DAY_MONTH_YEAR, _MONTH_YEAR):
        for m in rx.finditer(text):
            if free(m.start(), m.end()):
                found.append(_month_span(int(m.group(2)), _month_of(m.group(1)), m.start()))
                taken.append((m.start(), m.end()))
    for m in _NEXT_MONTH.finditer(text):
        if free(m.start(), m.end()):
            month = _month_of(m.group(1))
            year = now.year + (1 if month <= now.month else 0)
            found.append(_month_span(year, month, m.start()))
            taken.append((m.start(), m.end()))
    for m in _BARE_YEAR.finditer(text):
        if free(m.start(), m.end()):
            found.append(_year_span(int(m.group(1)), m.start()))
            taken.append((m.start(), m.end()))
    for rx, year in ((_THIS_YEAR, now.year), (_NEXT_YEAR, now.year + 1)):
        for m in rx.finditer(text):
            if free(m.start(), m.end()):
                found.append(_year_span(year, m.start()))
                taken.append((m.start(), m.end()))
    return sorted(found, key=lambda w: w.pos)


def _near(moment: datetime, when: _When) -> bool:
    return when.lo - SLACK <= moment <= when.hi + SLACK


def _day(dt: datetime) -> str:
    return f"{dt:%d %b %Y}"


# --- segmenting ----------------------------------------------------------------------


def _graha_hits(text: str) -> list[tuple[int, Graha]]:
    hits: list[tuple[int, Graha]] = []
    masked = _GURU_JI.sub(lambda m: " " * len(m.group(0)), text)
    for g, rx in _GRAHA_RE.items():
        hits.extend((m.start(), g) for m in rx.finditer(masked))
    hits.extend((m.start(), Graha.SUN) for m in _SUN_EN.finditer(masked))
    return sorted(hits)


def _segments(sentence: str) -> list[tuple[Graha, str]]:
    """Each graha owns the text from its mention to the next graha's; the first also owns
    what comes before it ("In April 2027, Saturn antardasha starts")."""
    hits = _graha_hits(sentence)
    out: list[tuple[Graha, str]] = []
    for i, (pos, g) in enumerate(hits):
        start = 0 if i == 0 else pos
        end = hits[i + 1][0] if i + 1 < len(hits) else len(sentence)
        out.append((g, sentence[start:end]))
    return out


def _level(text: str) -> str | None:
    if _PD.search(text):
        return "pd"
    if _MD.search(text):
        return "md"
    if _AD.search(text):
        return "ad"
    if _BARE_DASHA.search(text):
        return "dasha"
    return None


def _houses(text: str) -> set[int]:
    found = {int(m.group(1)) for m in _HOUSE_NUM.finditer(text)}
    found |= {n for n, rx in _HOUSE_WORD.items() if rx.search(text)}
    return found


def _signs(text: str) -> set[str]:
    found = {s for s, rx in _SIGN_RE.items() if rx.search(text)}
    found |= {s for s, rx in _RASHI_RE.items() if rx.search(text)}
    return found


# --- the checker ---------------------------------------------------------------------


class _Checker:
    def __init__(
        self, dossier: Dossier, now: datetime, sky: Sky | None, transits: TransitSnapshot | None
    ) -> None:
        self.d = dossier
        self.now = now
        self.sky = sky
        self.transits = transits
        self.chain = dasha_at(dossier.dasha.mahadashas, now)

    # dasha periods

    def _instances(self, level: str, lord: Graha) -> list[tuple[DashaPeriod, str]]:
        """Every period of `lord` at `level`, with a label naming its parents."""
        out: list[tuple[DashaPeriod, str]] = []
        for md in self.d.dasha.mahadashas:
            if level == "md" and md.lord == lord:
                out.append((md, f"{lord} mahadasha"))
            for ad in md.sub:
                if level == "ad" and ad.lord == lord:
                    out.append((ad, f"{lord} antardasha (in {md.lord} mahadasha)"))
                if level == "pd" and abs((ad.start - self.now).days) < 6 * 366:
                    out.extend(
                        (pd, f"{lord} pratyantardasha (in {md.lord}/{ad.lord})")
                        for pd in subperiods(ad)
                        if pd.lord == lord
                    )
        return out

    def _running(self, level: str) -> DashaPeriod | None:
        i = {"md": 0, "ad": 1}.get(level)
        if i is not None:
            return self.chain[i] if len(self.chain) > i else None
        if len(self.chain) > 1:
            return next((p for p in subperiods(self.chain[1]) if p.contains(self.now)), None)
        return None

    def _next_ad(self) -> DashaPeriod | None:
        ads = [ad for md in self.d.dasha.mahadashas for ad in md.sub]
        return next((a for a in ads if a.start > self.now), None)

    def running_fact(self) -> str:
        names = ("mahadasha", "antardasha", "pratyantardasha")
        chain = list(self.chain)
        pd = self._running("pd")
        if pd is not None:
            chain.append(pd)
        now = "; ".join(
            f"{p.lord} {names[i]} {_day(p.start)} to {_day(p.end)}" for i, p in enumerate(chain)
        )
        nxt = self._next_ad()
        tail = f"; next antardasha: {nxt.lord} from {_day(nxt.start)}" if nxt else ""
        return f"Running now: {now}{tail}."

    def dasha(self, lord: Graha, level: str, seg: str, sentence: str) -> list[Problem]:
        levels = ["md", "ad"] if level == "dasha" else [level]
        dates = _dates(seg, self.now)
        name = {"md": "mahadasha", "ad": "antardasha", "pd": "pratyantardasha", "dasha": "dasha"}[
            level
        ]
        said = f"{lord} {name}"
        if not dates:
            if _NEXT.search(seg) and level == "ad" and not _NOW.search(seg):
                nxt = self._next_ad()
                if nxt is not None and nxt.lord != lord:
                    return [Problem(sentence, f"{said} comes next", self.running_fact())]
                return []
            if _NOW.search(seg) and not _NEXT.search(seg):
                running = [p for p in (self._running(lv) for lv in levels) if p is not None]
                if running and all(p.lord != lord for p in running):
                    return [Problem(sentence, f"{said} is running now", self.running_fact())]
            return []

        instances = [x for lv in levels for x in self._instances(lv, lord)]
        if not instances:
            return []
        start_kw, end_kw = bool(_START.search(seg)), bool(_END.search(seg))

        def ok(p: DashaPeriod) -> bool:
            if len(dates) >= 2 and (end_kw or re.search(r"\bto\b|\u2013|\u2014", seg)):
                return _near(p.start, dates[0]) and _near(p.end, dates[-1])
            w = dates[0]
            if start_kw and not end_kw:
                return _near(p.start, w)
            if end_kw and not start_kw:
                return _near(p.end, w)
            if start_kw and end_kw:  # one date, both words: either reading
                return _near(p.start, w) or _near(p.end, w)
            return p.start - SLACK <= w.hi and w.lo <= p.end + SLACK  # "in 2027": overlaps

        if any(ok(p) for p, _ in instances):
            return []
        near = sorted(instances, key=lambda x: abs((x[0].start - self.now).total_seconds()))[:2]
        periods = "; ".join(f"{label}: {_day(p.start)} to {_day(p.end)}" for p, label in near)
        return [
            Problem(sentence, f"{said}, dated {seg.strip()!r}", f"{periods}. {self.running_fact()}")
        ]

    # placements

    def _natal(self, g: Graha) -> tuple[int, str]:
        p = next(x for x in self.d.d1.grahas if x.graha == g)
        return p.house, p.sign

    def _house_label(self, h: int) -> str:
        basis = "lagna" if self.d.d1.house_basis == "lagna" else "the Moon (birth time unknown)"
        return f"{h}th house from {basis}"

    def placement(self, g: Graha, seg: str, sentence: str) -> list[Problem]:
        houses, signs = _houses(seg), _signs(seg)
        if len(houses) + len(signs) != 1:
            return []  # nothing, or several: too ambiguous to judge
        dates = _dates(seg, self.now)
        moving = bool(_ENTER.search(seg))
        transit = bool(_TRANSIT.search(seg)) or moving
        if (transit or moving) and dates:
            return self._dated_transit(g, signs, dates[0], sentence) if signs else []
        now_t = self._transit_now(g, houses, signs)
        natal_h, natal_s = self._natal(g)
        natal_ok = (houses == {natal_h}) if houses else (signs == {natal_s})
        if transit:
            ok = now_t
        elif _NOW.search(seg):
            ok = natal_ok or now_t  # "Saturn in your 7th is now testing you": either reading
        else:
            ok = natal_ok
        if ok is None or ok:
            return []
        claim = (
            f"{g} in the {next(iter(houses))}th house" if houses else f"{g} in {next(iter(signs))}"
        )
        facts = [f"Natal {g}: {natal_s}, {self._house_label(natal_h)}."]
        if self.transits is not None:
            t = self.transits.graha(g)
            facts.append(
                f"Transiting {g} now: {t.sign}"
                + (f", {t.house_from_lagna}th from lagna" if t.house_from_lagna else "")
                + f", {t.house_from_moon}th from the Moon."
            )
        return [Problem(sentence, claim, " ".join(facts))]

    def _transit_now(self, g: Graha, houses: set[int], signs: set[str]) -> bool | None:
        if self.transits is None:
            return None
        t = self.transits.graha(g)
        if signs:
            return signs == {t.sign}
        return bool(houses & {t.house_from_lagna, t.house_from_moon})

    def _dated_transit(
        self, g: Graha, signs: set[str], when: _When, sentence: str
    ) -> list[Problem]:
        if self.sky is None:
            return []
        lo, hi = when.lo - SLACK, when.hi + SLACK
        steps = max(2, min(_MAX_TRANSIT_SAMPLES, (hi - lo).days // 15))
        seen: set[str] = set()
        try:
            for k in range(steps + 1):
                at = lo + (hi - lo) * k / steps
                seen.add(transit_snapshot(self.sky, self.d.d1, at).graha(g).sign)
        except OutOfRangeError:
            return []
        if signs & seen:
            return []
        sign = next(iter(signs))
        return [
            Problem(
                sentence,
                f"{g} in {sign} around {when.lo:%b %Y}",
                f"Between {_day(lo)} and {_day(hi)} {g} transits: {', '.join(sorted(seen))}.",
            )
        ]

    def lagna_or_rashi(self, sentence: str) -> list[Problem]:
        signs = _signs(sentence)
        if len(signs) != 1 or _houses(sentence) or not _YOUR.search(sentence):
            return []
        sign = next(iter(signs))
        if _LAGNA.search(sentence) and not _RASHI.search(sentence):
            lagna = self.d.d1.lagna
            if lagna is not None and lagna.sign != sign:
                return [Problem(sentence, f"lagna {sign}", f"The lagna is {lagna.sign}.")]
        elif _RASHI.search(sentence) and not _LAGNA.search(sentence):
            moon = self._natal(Graha.MOON)[1]
            if "moon_sign" not in self.d.uncertain and moon != sign:
                return [Problem(sentence, f"Moon sign {sign}", f"The Moon sign (rashi) is {moon}.")]
        return []

    def sade_sati(self, sentence: str) -> list[Problem]:
        if self.transits is None or not _NOW.search(sentence) or _dates(sentence, self.now):
            return []
        if self.transits.sade_sati_phase is None:
            spans = "; ".join(f"{_day(s.start)} to {_day(s.end)}" for s in self.d.sade_sati)
            return [
                Problem(
                    sentence,
                    "Sade Sati is running now",
                    f"Sade Sati is not running now. Sade Sati periods: {spans or 'none'}.",
                )
            ]
        return []


def check_reply(
    text: str,
    dossier: Dossier,
    now: datetime,
    *,
    sky: Sky | None = None,
    transits: TransitSnapshot | None = None,
) -> list[Problem]:
    """Wrong claims in `text` (empty when everything checkable is right)."""
    checker = _Checker(dossier, now, sky, transits)
    problems: list[Problem] = []
    for raw in _SENTENCES.split(text):
        sentence = raw.strip()
        if not sentence or _SKIP.search(sentence):
            continue
        if _SADE_SATI.search(sentence):
            problems.extend(checker.sade_sati(sentence))
            continue
        segments = _segments(sentence)
        if not segments:
            problems.extend(checker.lagna_or_rashi(sentence))
            continue
        inherited: str | None = None
        for g, seg in segments:
            level = _level(seg)
            if level is None and inherited and (_dates(seg, now) or _START.search(seg)):
                level = inherited  # "Jupiter antardasha now, and Saturn's from 2027"
            if level is not None:
                inherited = level
                problems.extend(checker.dasha(g, level, seg, sentence))
            else:
                problems.extend(checker.placement(g, seg, sentence))
    return problems


def feedback(problems: list[Problem]) -> str:
    """The correction the model gets for a rewrite."""
    lines = [f'- You wrote "{p.sentence}" ({p.said}), but: {p.fact}' for p in problems]
    return (
        "Some facts in your last reply do not match this chart:\n"
        + "\n".join(lines)
        + "\nRewrite the reply with these facts corrected (keep the same warmth, language and "
        "length; leave out anything you cannot state correctly)."
    )


def strip_wrong(text: str, problems: list[Problem]) -> str:
    """Last line of defence: drop the sentences that still carry a wrong claim."""
    bad = {p.sentence for p in problems}
    paras = []
    for para in re.split(r"\n\s*\n", text):
        kept = [s for s in _SENTENCES.split(para) if s.strip() and s.strip() not in bad]
        if kept:
            paras.append(" ".join(s.strip() for s in kept))
    return "\n\n".join(paras)
