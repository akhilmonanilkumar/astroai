"""Output guardrails for guru replies (CLAUDE.md: no death/lifespan predictions, medical
diagnoses, trading calls, legal verdicts or guaranteed outcomes; never claim to be human).

The guru's style check sends a violating reply back for one rewrite; if the rewrite still
violates, the graph replaces it with a scripted fallback. Patterns target the forbidden
claim, not the topic: "health" or "money" alone are fine.
"""

import re

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "death or lifespan prediction",
        re.compile(
            r"\b(you|he|she|they|your \w+) (will|may|might|could) die\b|"
            r"\b(date|time|year|age) of (your |his |her )?death\b|\blifespan\b|"
            r"\b(short|long) life\b|\bearly death\b|\bmaut (ho|aa) (sakti|jayegi|gi)\b|"
            r"\bmrityu yog\b|\balp ?ayu\b|मृत्यु\s*योग|अल्पायु|मौत\s*(हो|आ)",
            re.IGNORECASE,
        ),
    ),
    (
        "medical diagnosis or treatment",
        re.compile(
            r"\byou (have|suffer from|are suffering from) (cancer|diabetes|tumou?r|"
            r"depression|a disease|an illness|heart disease)\b|"
            r"\b(stop|quit|skip) (taking )?(your )?(medicine|medication|tablets|treatment)\b|"
            r"\b(no need|don'?t need) (for |to see )?(a )?doctor\b|"
            r"\bdawai (band|chhod)\b|दवा(ई)?\s*(बंद|छोड़)",
            re.IGNORECASE,
        ),
    ),
    (
        "trading or investment call",
        re.compile(
            r"\b(buy|sell|invest in|short) (this |the )?(stock|shares?|crypto|bitcoin|"
            r"options|futures)\b|\b(stock|share|crypto) (price )?will (rise|go up|fall|crash)\b|"
            r"\b(lottery|satta|betting) (number|jeet)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "legal verdict",
        re.compile(
            r"\byou will (definitely |surely )?(win|lose) (the|your|this) (case|lawsuit|court)\b|"
            r"\b(case|mukadma) (pakka|zaroor) (jeetenge|haarenge)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "guaranteed outcome",
        re.compile(
            r"\b(100 ?%|hundred percent|guaranteed?|guarantee(d)? that)\b|"
            r"\b(definitely|certainly|surely) (will|going to) (happen|get|marry|succeed)\b|"
            r"\bpakka (hoga|milega|hogi)\b|\bguarantee hai\b|\bगारंटी\b",
            re.IGNORECASE,
        ),
    ),
    (
        "fear-based upsell",
        re.compile(
            r"\b(disaster|destruction|ruin|curse) will (come|fall|follow)\b|"
            r"\bif you don'?t (do|perform|buy) (this|the) (puja|remedy|gem(stone)?)\b",
            re.IGNORECASE,
        ),
    ),
)


def violation(text: str) -> str | None:
    """Name of the first guardrail the text breaks, or None."""
    for name, rx in _RULES:
        if rx.search(text):
            return name
    return None
