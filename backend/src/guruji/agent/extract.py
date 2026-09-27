"""Pull birth details out of free-text onboarding answers.

Rules first (cheap, deterministic, cover most answers); the fast LLM only when the rules
miss the field that was just asked for. Every value is validated here either way, so the
LLM can suggest but never put an impossible date or time into a chart.
Numeric dates are read day-first (Indian convention); the confirm step catches mistakes.
"""

import datetime as dt
import logging
import re
import unicodedata
from datetime import date, time
from typing import Literal

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel
from pydantic import Field as SchemaField

log = logging.getLogger(__name__)

Field = Literal["name", "date", "time", "place"]
MIN_YEAR = 1900

_MONTHS: dict[str, int] = {}
for _i, _names in enumerate(
    [
        "january jan janvari janwari जनवरी",
        "february feb farvari farwari फरवरी फ़रवरी",
        "march mar marc मार्च",
        "april apr aprail अप्रैल अप्रेल",
        "may mai मई",
        "june jun जून",
        "july jul julai जुलाई",
        "august aug agast अगस्त",
        "september sep sept sitambar सितंबर सितम्बर",
        "october oct aktubar aktoobar अक्टूबर",
        "november nov navambar नवंबर नवम्बर",
        "december dec disambar दिसंबर दिसम्बर",
    ],
    start=1,
):
    for _n in _names.split():
        _MONTHS[_n] = _i
_MONTH_RE = "|".join(sorted(map(re.escape, _MONTHS), key=len, reverse=True))

_ISO = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})\b")
_DAY_MONTH = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s+|tarikh\s+|तारीख\s+)?({_MONTH_RE})\.?,?\s*(\d{{2}}|\d{{4}})\b",
    re.IGNORECASE,
)
_MONTH_DAY = re.compile(
    rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*(\d{{4}})\b", re.IGNORECASE
)

_HM = re.compile(r"\b(\d{1,2})[:.](\d{2})\s*(a\.?m\.?|p\.?m\.?)?", re.IGNORECASE)
_H_AMPM = re.compile(r"\b(\d{1,2})\s*(a\.?m\.?|p\.?m\.?)(?![a-z])", re.IGNORECASE)
_H_BAJE = re.compile(r"\b(\d{1,2})\s*(?:baje|bje|बजे|o'?clock)", re.IGNORECASE)
_MORNING = re.compile(r"subah|subha|savere|morning|सुबह|सवेरे|प्रातः", re.IGNORECASE)
_AFTERNOON = re.compile(r"dopahar|dopehar|afternoon|दोपहर", re.IGNORECASE)
_EVENING = re.compile(r"shaam|sham\b|evening|शाम", re.IGNORECASE)
_NIGHT = re.compile(r"raat|rat\b|night|रात", re.IGNORECASE)
_UNKNOWN_TIME = re.compile(
    r"don'?t know|do not know|not sure|no idea|unknown|dont remember|don'?t remember|"
    r"pata nahi|pata nhi|nahi pata|nhi pata|maloom nahi|malum nahi|yaad nahi|"
    r"पता नहीं|मालूम नहीं|याद नहीं",
    re.IGNORECASE,
)

_NAME_PREFIX = re.compile(
    r"^(?:(?:hi|hello|namaste|namaskar|ji)[,!\s]+)*"
    r"(?:my name is|my name's|i am|i'm|im|this is|it's|its|name is|name|mera naam|naam|"
    r"main|mai|मेरा नाम|मैं)\s*[:\-]?\s*",
    re.IGNORECASE,
)
_NAME_SUFFIX = re.compile(r"\s+(?:hai|he|है|here)\s*[.!]*$", re.IGNORECASE)
_NAME_JOINERS = frozenset(".'-")

_PLACE_FILLER = re.compile(
    r"\b(?:i was born in|i was born at|born in|born at|in|at|mera janam|janam|hua tha|hua|"
    r"tha|mein|me|city|the|place is|place|birthplace|jagah)\b|जन्म|में|हुआ|था|जगह",
    re.IGNORECASE,
)
# "... in Kochi", "... at Delhi", "Kochi mein": a place offered alongside another answer.
_VOLUNTEERED_PLACE = re.compile(
    r"(?:\bborn in\b|\bborn at\b|\bin\b|\bat\b|\bplace is\b|\bjagah\b)\s+(.+)$"
    r"|(\S+(?:\s\S+)?)\s+(?:mein|में)(?!\w)",
    re.IGNORECASE,
)
_NAME_END = re.compile(r"[,;]|\b(?:born|dob|janam|date)\b|\d|जन्म", re.IGNORECASE)
_TIME_ANY = re.compile(
    r"\b\d{1,2}(?:[:.]\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|baje|bje|बजे)?", re.IGNORECASE
)


_DETAILED_WORDS = 6


class BirthFields(BaseModel):
    name: str | None = None
    date: dt.date | None = None
    time: dt.time | None = None
    time_unknown: bool = False
    place: str | None = None
    # A place-like phrase in an answer to another question: used only if it is found.
    place_guess: str | None = None

    def merged_over(self, old: "BirthFields") -> "BirthFields":
        data = old.model_dump()
        for k, v in self.model_dump().items():
            if v not in (None, False):
                data[k] = v
        if self.time is not None:
            data["time_unknown"] = False
        if self.time_unknown:
            data["time"] = None
        return BirthFields(**data)


def _year(y: str, today: date) -> int:
    n = int(y)
    if len(y) == 2:
        n = 2000 + n if 2000 + n <= today.year else 1900 + n
    return n


def valid_date(y: int, m: int, d: int, today: date) -> date | None:
    try:
        value = date(y, m, d)
    except ValueError:
        return None
    return value if value.year >= MIN_YEAR and value <= today else None


def parse_date(text: str, today: date) -> tuple[date | None, str]:
    """(date, text with the date removed)."""
    for rx in (_ISO, _NUMERIC, _DAY_MONTH, _MONTH_DAY):
        m = rx.search(text)
        if not m:
            continue
        a, b, c = m.groups()
        if rx is _ISO:
            value = valid_date(int(a), int(b), int(c), today)
        elif rx is _NUMERIC:
            day, month = int(a), int(b)
            if month > 12 >= day:  # clearly month-first
                day, month = month, day
            value = valid_date(_year(c, today), month, day, today)
        elif rx is _DAY_MONTH:
            value = valid_date(_year(c, today), _MONTHS[b.casefold()], int(a), today)
        else:
            value = valid_date(_year(c, today), _MONTHS[a.casefold()], int(b), today)
        if value is not None:
            return value, text[: m.start()] + " " + text[m.end() :]
    return None, text


def _apply_period(hour: int, text: str, ampm: str | None) -> int | None:
    if ampm:
        pm = ampm.casefold().startswith("p")
        if not 1 <= hour <= 12:
            return None
        return (hour % 12) + (12 if pm else 0)
    if hour > 23:
        return None
    if hour >= 13 or hour == 0:
        return hour
    if _MORNING.search(text):
        return hour % 12
    if _AFTERNOON.search(text) or _EVENING.search(text):
        return hour % 12 + 12
    if _NIGHT.search(text):
        return hour % 12 + 12 if hour >= 6 else hour % 12  # raat 2 baje = 02:00
    return hour


def parse_time(text: str) -> tuple[time | None, bool]:
    """(time, unknown). Pass text with any date already removed."""
    if _UNKNOWN_TIME.search(text):
        return None, True
    m = _HM.search(text)
    if m:
        hour = _apply_period(int(m.group(1)), text, m.group(3))
        minute = int(m.group(2))
        if hour is not None and minute < 60:
            return time(hour, minute), False
    m = _H_AMPM.search(text) or _H_BAJE.search(text)
    if m:
        ampm = m.group(2) if m.re is _H_AMPM else None
        hour = _apply_period(int(m.group(1)), text, ampm)
        if hour is not None:
            return time(hour, 0), False
    return None, False


def _looks_like_name(t: str) -> bool:
    """1-5 words of letters in any script. Combining marks count too: Devanagari vowel
    signs are marks, which the regex word class does not match."""
    words = t.split()
    if not 1 <= len(words) <= 5 or unicodedata.category(t[0])[0] != "L":
        return False
    return all(unicodedata.category(c)[0] in "LM" or c in _NAME_JOINERS for w in words for c in w)


def parse_name(text: str) -> str | None:
    t = _NAME_SUFFIX.sub("", _NAME_PREFIX.sub("", text.strip())).strip(" .!,")
    if not t or len(t) > 60 or not _looks_like_name(t):
        return None
    return " ".join(w[:1].upper() + w[1:] for w in t.split())


def clean_place(text: str) -> str | None:
    t = _PLACE_FILLER.sub(" ", text)
    t = re.sub(r"\s+", " ", t).strip(" .!,")
    return t if len(t) >= 2 and not t.isdigit() else None


def rule_extract(text: str, asking: Field | None, today: date) -> BirthFields:
    """Values the rules can read. A place not asked for is only a guess (`place_guess`)."""
    found = BirthFields()
    d, rest = parse_date(text, today)
    found.date = d
    t, unknown = parse_time(rest)
    if asking in ("time", None) or t is not None:
        found.time, found.time_unknown = t, unknown and asking == "time"
    if asking == "name":
        # "Meera", or "Meera, born 5 March 1988 in Bombay": the name leads.
        head = text if d is None and t is None else _NAME_END.split(text, maxsplit=1)[0]
        found.name = parse_name(head)
    if asking == "place" and d is None and t is None:
        found.place = clean_place(text)
        return found
    m = _VOLUNTEERED_PLACE.search(_TIME_ANY.sub(" ", rest))
    if m:
        found.place_guess = clean_place(m.group(1) or m.group(2))
    elif asking is None and d is None and t is None:
        found.place_guess = clean_place(text)
    return found


class _LLMFields(BaseModel):
    """Birth details stated in the message. Use null for anything not stated."""

    name: str | None = SchemaField(None, description="The user's own name, if stated")
    date: str | None = SchemaField(None, description="Date of birth as YYYY-MM-DD")
    time: str | None = SchemaField(None, description="Time of birth as 24-hour HH:MM")
    time_unknown: bool = SchemaField(False, description="True only if they say they don't know")
    place: str | None = SchemaField(None, description="Birth town or city, with state or country")


_PROMPT = """You read one WhatsApp message from a user who is giving their birth details \
to an astrologer. Today is {today}. The question they were just asked: {asking}.
Extract only what the message states. Indian users write numeric dates day-first \
(05/07/1990 = 5 July 1990). Convert times to 24-hour HH:MM using words like subah \
(morning), dopahar (afternoon), shaam (evening), raat (night). Set time_unknown only if \
they say they do not know their birth time. For place, give the town/city name as written, \
with state or country if they gave it. Never guess missing details.

Message: {text}"""

_QUESTIONS: dict[Field | None, str] = {
    "name": "What is your name?",
    "date": "What is your date of birth?",
    "time": "What time were you born?",
    "place": "Where were you born?",
    None: "Please share or correct your birth details.",
}


async def llm_extract(
    model: BaseChatModel, text: str, asking: Field | None, today: date
) -> BirthFields:
    try:
        structured = model.with_structured_output(_LLMFields, method="json_schema")
        raw = await structured.ainvoke(
            _PROMPT.format(today=today.isoformat(), asking=_QUESTIONS[asking], text=text)
        )
    except Exception as e:  # extraction is best effort; the rules still ran
        log.warning("llm extraction failed: %s", type(e).__name__)
        return BirthFields()
    if not isinstance(raw, _LLMFields):
        return BirthFields()
    out = BirthFields(time_unknown=raw.time_unknown)
    if raw.name:
        out.name = parse_name(raw.name)
    # Models don't always follow the requested formats ("15 July 1990", "9:00 AM"):
    # read their values with the same validated parsers as user text.
    if raw.date:
        out.date = parse_date(raw.date, today)[0]
    if raw.time and not raw.time_unknown:
        out.time = parse_time(raw.time)[0]
    if raw.place:
        out.place = clean_place(raw.place) or raw.place.strip()[:80]
    return out


async def extract(
    text: str, asking: Field | None, today: date, model: BaseChatModel | None
) -> BirthFields:
    found = rule_extract(text, asking, today)
    missing = asking is None or getattr(found, asking) in (None, False)
    if asking == "time" and found.time_unknown:
        missing = False
    # A long answer usually carries more than was asked ("I'm Priya, born ... in ...").
    detailed = len(text.split()) >= _DETAILED_WORDS
    if model is not None and (missing or detailed) and len(text.strip()) >= 2:
        llm = await llm_extract(model, text, asking, today)
        # Rule values are validated and exact; the model fills what they missed.
        return found.merged_over(llm)
    return found
