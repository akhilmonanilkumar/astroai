"""Account commands a user can send at any time, answered by code, never by the model.

Only short messages that are clearly the command count ("balance", "mera balance kitna
hai"), so a question that merely mentions credits still reaches Guruji.
"""

import re
from typing import Literal

Command = Literal["balance"]

_MAX_WORDS = 6
_BALANCE = re.compile(
    r"^\W*(?:my\s+|mera\s+|meri\s+)?(?:balance|credits?|wallet|बैलेंस|क्रेडिट)"
    r"(?:\s+(?:kitna|kitne|kya|hai|hain|left|check|please|pls|कितना|कितने|है|हैं))*\W*$"
    r"|^\W*(?:how many credits|kitne credits?|kitna balance)\b.*$",
    re.IGNORECASE,
)


def detect_command(text: str) -> Command | None:
    t = text.strip()
    if not t or len(t.split()) > _MAX_WORDS:
        return None
    if _BALANCE.match(t):
        return "balance"
    return None
