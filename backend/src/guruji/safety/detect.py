"""Signals that take a turn out of astrology: crisis, emergencies, abuse, asking for a human.

Rules here are deterministic and run on every message, in every user state (even before
consent: someone in crisis gets help regardless). They favour recall for the most serious
category; the fast model adds a second opinion for phrasings the rules miss.
"""

import re
from dataclasses import dataclass
from typing import Literal

Category = Literal["crisis", "medical", "abuse", "legal", "human_requested"]
# escalations.severity: 1 = most urgent
SEVERITY: dict[Category, int] = {
    "crisis": 1,
    "medical": 1,
    "abuse": 1,
    "legal": 2,
    "human_requested": 3,
}


@dataclass(frozen=True)
class Signal:
    category: Category
    severity: int


def _rx(*phrases: str) -> re.Pattern[str]:
    return re.compile("|".join(phrases), re.IGNORECASE)


# Self-harm and suicidal thoughts. Broad on purpose: a false alarm costs a kind message.
_CRISIS = _rx(
    r"\b(kill|hurt|harm|cut)\s+(my ?self)\b",
    r"\bsuicid",
    r"\b(want|wanna|going|plan(ning)?)\s+to\s+(die|end (it|my life|everything))\b",
    r"\bend(ing)? my life\b",
    r"\b(don'?t|do not|no longer) want to (live|be alive|wake up)\b",
    r"\bno (reason|point) (to|in) (live|living)\b",
    r"\bbetter off dead\b",
    r"\bkhud ?kushi\b",
    r"\baatma ?hatya\b|\batmhatya\b",
    r"\b(jeena|jina|jeene) nahi?n? (chahta|chahti|chahte)\b",
    r"\bmar (jana|jaana|jaun|jaunga|jaungi|jaana chahta|jaana chahti)\b",
    r"\bzindagi (khatam|khatm) (kar|karna)\b",
    r"\bmarna (chahta|chahti)\b",
    r"आत्महत्या|ख़ुदकुशी|खुदकुशी",
    r"(जीना|जीने)\s*नहीं\s*(चाहता|चाहती)",
    r"मर\s*(जाना|जाऊं|जाऊँ|जाऊंगा|जाऊंगी)",
    r"मरना\s*(चाहता|चाहती)",
    r"ज़िंदगी\s*(ख़त्म|खत्म)",
)
_MEDICAL = _rx(
    r"\b(chest pain|heart attack|can'?t breathe|cannot breathe|difficulty breathing)\b",
    r"\b(overdose|took (too many|all the) (pills|tablets)|swallowed poison|drank poison)\b",
    r"\b(unconscious|fainted and|not breathing|heavy bleeding|bleeding (a lot|heavily))\b",
    r"\b(stroke|seizure|fits aa)\b",
    r"\b(zeher|zehar|jahar) (kha|pi) (liya|li)\b",
    r"\b(saans nahi?n? (aa|le) (rahi|pa))\b",
    r"ज़हर|जहर\s*(खा|पी)\s*(लिया|ली)|साँस\s*नहीं|सीने\s*में\s*दर्द",
)
_ABUSE = _rx(
    r"\b(he|she|they|husband|wife|father|mother|in-?laws?)\s+(beats?|hits?|hurts?|abuses?)\s+me\b",
    r"\b(beating|hitting|abusing|threatening) me\b",
    r"\bdomestic (violence|abuse)\b",
    r"\b(sexual(ly)? (abuse|assault|harass)|raped?|molest)",
    r"\b(maarta|maarti|marta|marti|peet(ta|ti)) hai\b",
    r"\bmujhe (maarte|peet(te)?) hain\b",
    r"मारता\s*है|मारती\s*है|पीटता\s*है|मारपीट",
)
# The user's own legal emergency (a relative's case is an ordinary astrology worry).
_LEGAL = _rx(
    r"\b(i was|i am|i'm|i got|they) (just )?(arrested|detained)\b",
    r"\b(police (took|picked up|are taking) me|i am in (jail|custody)|FIR (against|on) me)\b",
    r"\bmujhe (police ne )?(giraftar|arrest) kar (liya|lenge)\b",
    r"मुझे\s*गिरफ्तार",
)
_HUMAN = _rx(
    r"\b(talk|speak|chat) (to|with) (a |an )?(real |actual )?"
    r"(human|person|astrologer|someone real)\b",
    r"\b(customer care|customer support|your team|real person)\b",
    r"\b(insaan|insan|asli (jyotishi|pandit|astrologer)) se baat\b",
    r"इंसान\s*से\s*बात|असली\s*(ज्योतिषी|पंडित)",
)
_ORDER: tuple[tuple[Category, re.Pattern[str]], ...] = (
    ("crisis", _CRISIS),
    ("medical", _MEDICAL),
    ("abuse", _ABUSE),
    ("legal", _LEGAL),
    ("human_requested", _HUMAN),
)


def detect(text: str) -> Signal | None:
    """The most serious signal in the text, or None."""
    for category, rx in _ORDER:
        if rx.search(text):
            return Signal(category, SEVERITY[category])
    return None


def looks_urgent(text: str) -> bool:
    """For queue priority: severity-1 signals jump ahead of ordinary turns."""
    s = detect(text)
    return s is not None and s.severity == 1
