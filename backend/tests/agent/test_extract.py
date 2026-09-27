from datetime import date, time
from typing import Any

import pytest

from guruji.agent.extract import BirthFields, extract, rule_extract
from guruji.agent.language import detect, update

TODAY = date(2026, 9, 28)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("15/07/1990", date(1990, 7, 15)),
        ("07-15-1990", date(1990, 7, 15)),  # month-first only when unambiguous
        ("05/07/1990", date(1990, 7, 5)),  # day-first by default
        ("15th July 1990", date(1990, 7, 15)),
        ("July 15, 1990", date(1990, 7, 15)),
        ("15 july 92", date(1992, 7, 15)),
        ("3 march 05", date(2005, 3, 3)),
        ("1990-07-15", date(1990, 7, 15)),
        ("मेरा जन्म 15 जुलाई 1990", date(1990, 7, 15)),
        ("15 tarikh julai 1990", date(1990, 7, 15)),
        ("31/02/1990", None),
        ("15/07/2030", None),  # future
        ("15/07/1850", None),
    ],
)
def test_dates(text: str, want: date | None) -> None:
    assert rule_extract(text, "date", TODAY).date == want


@pytest.mark.parametrize(
    ("text", "want", "unknown"),
    [
        ("9:30 am", time(9, 30), False),
        ("9.30 pm", time(21, 30), False),
        ("12:15 am", time(0, 15), False),
        ("subah 9 baje", time(9, 0), False),
        ("raat 11 baje", time(23, 0), False),
        ("raat 2 baje", time(2, 0), False),
        ("shaam 6:45", time(18, 45), False),
        ("21:15", time(21, 15), False),
        ("10 pm", time(22, 0), False),
        ("सुबह 9 बजे", time(9, 0), False),
        ("pata nahi", None, True),
        ("I don't know the time", None, True),
        ("25:00", None, False),
    ],
)
def test_times(text: str, want: time | None, unknown: bool) -> None:
    got = rule_extract(text, "time", TODAY)
    assert (got.time, got.time_unknown) == (want, unknown)


def test_date_digits_are_not_read_as_time() -> None:
    got = rule_extract("15.07.1990", "time", TODAY)
    assert got.date == date(1990, 7, 15) and got.time is None


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Rahul", "Rahul"),
        ("my name is priya sharma", "Priya Sharma"),
        ("mera naam Amit hai", "Amit"),
        ("Namaste, I'm Lakshmi", "Lakshmi"),
        ("मेरा नाम अनीता है", "अनीता"),
        ("Radhe", "Radhe"),
        ("D'Souza", "D'Souza"),
        ("ok 123", None),
        ("", None),
    ],
)
def test_names(text: str, want: str | None) -> None:
    assert rule_extract(text, "name", TODAY).name == want


def test_everything_in_the_name_answer() -> None:
    got = rule_extract("Meera, born 5th March 1988 in Bombay", "name", TODAY)
    assert (got.name, got.date, got.place_guess) == ("Meera", date(1988, 3, 5), "Bombay")
    got = rule_extract("Arjun 12/01/1991 subah 6 baje, Pune mein", "name", TODAY)
    assert (got.name, got.date, got.time) == ("Arjun", date(1991, 1, 12), time(6, 0))
    assert got.place_guess == "Pune"
    # A stray "in the morning" is only a guess; onboarding drops it unless it is a real place.
    assert rule_extract("9 in the morning", "time", TODAY).place_guess == "morning"


def test_places() -> None:
    assert rule_extract("I was born in Kochi", "place", TODAY).place == "Kochi"
    assert rule_extract("mera janam Varanasi mein hua tha", "place", TODAY).place == "Varanasi"


def test_merge_keeps_old_values_and_time_unknown_clears_time() -> None:
    old = BirthFields(name="Asha", time=time(9, 0))
    new = BirthFields(time_unknown=True).merged_over(old)
    assert (new.name, new.time, new.time_unknown) == ("Asha", None, True)


class _Structured:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls = 0

    async def ainvoke(self, prompt: str) -> Any:
        self.calls += 1
        return self.result


class _StubModel:
    def __init__(self, result: Any) -> None:
        self.structured = _Structured(result)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> _Structured:
        self.structured.result = schema(**self.structured.result)
        return self.structured


async def test_llm_fills_what_rules_miss_and_is_validated() -> None:
    model = _StubModel(
        {"name": "rahul", "date": "15 July 1990", "time": "27:00", "place": "Kochi, Kerala"}
    )
    got = await extract("main rahul, kochi wala", "name", TODAY, model)  # type: ignore[arg-type]
    assert got.name == "Rahul"
    assert got.date == date(1990, 7, 15)
    assert got.time is None  # invalid time rejected
    assert got.place == "Kochi, Kerala"


async def test_llm_not_called_when_rules_suffice() -> None:
    model = _StubModel({"date": "2000-01-01"})
    got = await extract("15/07/1990", "date", TODAY, model)  # type: ignore[arg-type]
    assert got.date == date(1990, 7, 15)
    assert model.structured.calls == 0


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("मेरी शादी कब होगी?", "hi"),
        ("meri shaadi kab hogi?", "hinglish"),
        ("mera promotion kab hoga", "hinglish"),
        ("namaste guruji, meri kundli dekhiye", "hinglish"),
        ("When will I get a promotion?", "en"),
        ("ok", None),
        ("15/07/1990", None),
    ],
)
def test_language(text: str, lang: str | None) -> None:
    assert detect(text) == lang


def test_language_is_sticky_for_short_answers() -> None:
    assert update("hinglish", "Meera, born 5th March 1988 in Bombay") == "hinglish"
    assert update("hinglish", "Actually I would prefer to talk in English please") == "en"
    assert update("en", "मेरा नाम मीरा है") == "hi"
    assert update(None, "ok") == "en"
