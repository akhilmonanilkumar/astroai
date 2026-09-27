"""Which language and script the user writes in, so Guruji can mirror it.

"hi" = Hindi in Devanagari, "hinglish" = Hindi/Hinglish in Latin script, "en" = English.
Cheap and deterministic; short or neutral messages ("ok", "1", "15/07/1990") return
None so the previously seen language is kept.
"""

import re
from typing import Literal

Language = Literal["en", "hi", "hinglish"]

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_WORD = re.compile(r"[a-z']+")
# Common Hindi words in Latin script that are rare in English.
_HINGLISH = frozenset(
    """
    hai hain ho hoon hu tha thi kya kyu kyun kaise kab kaha kahan mera meri mere mujhe
    mujhko hum hame humein aap aapka aapki tum tera teri nahi nahin nhi haan han ji bhi
    aur lekin pe se ko ka ki ke mein bahut bohot accha acha theek thik thoda
    kuch koi sab abhi kal aaj shaadi naukri paisa ghar baje subah shaam raat dopahar
    tarikh janam samay jagah batao bataiye bataye chahiye sakta sakti hoga hogi raha rahi
    dekhiye dekho dekhna dijiye kijiye karo karna kariye chahta chahti chahiye pooch puchna
    kab kaisa kaisi hoga milega milegi rahega
    """.split()
)
_MIN_WORDS = 2


def detect(text: str) -> Language | None:
    if _DEVANAGARI.search(text):
        return "hi"
    words = _WORD.findall(text.casefold())
    if len(words) < _MIN_WORDS:
        return None
    hindi = sum(w in _HINGLISH for w in words)
    if hindi >= 2 or hindi / len(words) >= 0.3:
        return "hinglish"
    return "en"


# Short answers (a name, a date, a city) look English whatever the user speaks.
_SWITCH_TO_EN_MIN_WORDS = 8


def update(previous: Language | None, text: str) -> Language:
    """The language to reply in: sticky, switching only on a clear signal."""
    found = detect(text)
    if found is None:
        return previous or "en"
    short = len(_WORD.findall(text.casefold())) < _SWITCH_TO_EN_MIN_WORDS
    if found == "en" and previous in ("hi", "hinglish") and short:
        return previous
    return found
