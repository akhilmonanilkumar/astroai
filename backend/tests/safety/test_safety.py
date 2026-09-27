import pytest

from guruji.safety.detect import detect, looks_urgent
from guruji.safety.guard import violation
from guruji.safety.messages import HOLDING, REPLIES, SAFE_FALLBACK


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("I want to die", "crisis"),
        ("i don't want to live anymore", "crisis"),
        ("thinking about suicide", "crisis"),
        ("I'm going to end it all tonight", "crisis"),
        ("I am going to end my life", "crisis"),
        ("ab jeena nahi chahti", "crisis"),
        ("khudkushi kar lunga", "crisis"),
        ("मैं आत्महत्या करना चाहता हूँ", "crisis"),
        ("मुझे जीना नहीं चाहता", "crisis"),
        ("my father has chest pain and can't breathe", "medical"),
        ("she took too many pills", "medical"),
        ("zeher kha liya", "medical"),
        ("my husband beats me", "abuse"),
        ("sasural wale maarte hain", None),
        ("pati maarta hai", "abuse"),
        ("police arrested my brother", None),
        ("I was arrested yesterday", "legal"),
        ("can I talk to a real person?", "human_requested"),
        ("kisi insaan se baat karni hai", "human_requested"),
        # ordinary astrology talk must not trip anything
        ("when will my marriage happen?", None),
        ("Saturn is killing my career lol", None),
        ("meri shaadi kab hogi", None),
        ("is my health okay this year?", None),
        ("death anniversary of my father is next week, is it a good day for puja?", None),
    ],
)
def test_detect(text: str, category: str | None) -> None:
    s = detect(text)
    assert (s.category if s else None) == category


def test_urgency() -> None:
    assert looks_urgent("I want to kill myself")
    assert not looks_urgent("talk to a human please")
    assert not looks_urgent("when will I get a job")


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("You will die young according to this chart.", "death or lifespan prediction"),
        ("Aapki kundli mein alpayu yog hai.", "death or lifespan prediction"),
        ("You have diabetes, the 6th house shows it.", "medical diagnosis or treatment"),
        ("You can stop taking your medicine after this puja.", "medical diagnosis or treatment"),
        ("Buy this stock now, Jupiter favours it.", "trading or investment call"),
        ("You will definitely win the case.", "legal verdict"),
        ("Marriage is 100% guaranteed next year.", "guaranteed outcome"),
        ("Shaadi pakka hogi March mein.", "guaranteed outcome"),
        ("If you don't do this puja, disaster will come.", "fear-based upsell"),
        # fine
        ("Saturn asks for care with health routines; please see a doctor.", None),
        ("The window from March to June looks strongest for marriage.", None),
        ("Jupiter favours steady, planned savings over quick trades.", None),
    ],
)
def test_guard(text: str, rule: str | None) -> None:
    assert violation(text) == rule


def test_scripted_messages_cover_every_language() -> None:
    for by_lang in REPLIES.values():
        assert set(by_lang) == {"en", "hinglish", "hi"}
        for bubbles in by_lang.values():
            assert 1 <= len(bubbles) <= 2
    assert all("14416" in "".join(REPLIES["crisis"][lang]) for lang in ("en", "hinglish", "hi"))
    assert all("112" in "".join(REPLIES["crisis"][lang]) for lang in ("en", "hinglish", "hi"))
    assert set(HOLDING) == set(SAFE_FALLBACK) == {"en", "hinglish", "hi"}
    for bubbles in REPLIES["crisis"].values():  # scripted replies pass the guardrails too
        assert all(violation(b) is None for b in bubbles)
