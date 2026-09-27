"""Which model answers a turn: the talk model or the reading model.

Talk: greetings, thanks, acknowledgements, small talk, feelings shared without a question.
Reading (real work): anything about their chart, life, future, timing, remedies, or an
answer to Guruji's clarifying question that leads into a reading.

Obvious small talk is caught by rules (no model call). Otherwise the fast model decides;
if it cannot, the turn goes to the reading model, trading cost for quality. The same call
gives a second opinion on safety, catching crisis phrasings the rules in
guruji.safety.detect miss.
"""

import logging
import re
from dataclasses import dataclass
from typing import Literal

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

Route = Literal["talk", "reading"]
SafetyFlag = Literal["none", "crisis", "medical", "abuse", "legal", "human_requested"]


@dataclass(frozen=True)
class Decision:
    route: Route
    safety: SafetyFlag = "none"


_SMALL_TALK = frozenset(
    """
    ok okay okk k kk hmm hmmm acha accha achha theek thik hai
    thanks thank you thankyou thx ty dhanyavaad dhanyawad shukriya bahut badhiya great nice
    good cool hi hello hey namaste namaskar pranam ram radhe jai shri krishna bye goodnight
    gn gm morning night good evening sure fine done got it
    ठीक है धन्यवाद शुक्रिया नमस्ते प्रणाम अच्छा बहुत बढ़िया
    """.split()
)
_WORDS = re.compile(r"[\w\u0900-\u097F]+")
_MAX_SMALL_TALK_WORDS = 5
# Deliberately absent: yes/no/haan/nahi/ji. Alone they often answer Guruji's clarifying
# question, which leads into a reading; the model sees the previous message and decides.


def obvious_small_talk(text: str) -> bool:
    words = _WORDS.findall(text.casefold())
    if not words:  # only emoji / punctuation, e.g. "🙏"
        return bool(text.strip())
    return len(words) <= _MAX_SMALL_TALK_WORDS and all(w in _SMALL_TALK for w in words)


class _Decision(BaseModel):
    """How to answer the user's latest WhatsApp message to their astrologer."""

    safety: SafetyFlag = Field(
        description=(
            "crisis: thoughts of suicide, self-harm or not wanting to live. medical: a medical "
            "emergency happening now. abuse: being hurt or threatened by someone. legal: the "
            "user themselves arrested or in custody. human_requested: asks for a human. "
            "none: anything else, including ordinary worries about health, money or family."
        )
    )
    kind: Literal["talk", "reading"] = Field(
        description=(
            "reading: they ask about their chart, life, future, timing, relationships, "
            "career, health, money, remedies or anything needing astrological analysis, "
            "or they answer the astrologer's clarifying question. talk: greeting, thanks, "
            "acknowledgement, small talk, or sharing feelings with no question."
        )
    )


_PROMPT = """Classify the user's latest message to a Vedic astrologer on WhatsApp.
Astrologer's previous message: {previous}
User's message: {text}"""


async def route_turn(model: BaseChatModel | None, text: str, previous: str | None) -> Decision:
    if obvious_small_talk(text):
        return Decision("talk")
    if model is None:
        return Decision("reading")
    try:
        out = await model.with_structured_output(_Decision, method="json_schema").ainvoke(
            _PROMPT.format(previous=(previous or "(none)")[:600], text=text[:1000])
        )
    except Exception as e:  # routing is best effort; a reading is the safe answer
        log.warning("turn routing failed: %s", type(e).__name__)
        return Decision("reading")
    if not isinstance(out, _Decision):
        return Decision("reading")
    return Decision(out.kind, out.safety)
