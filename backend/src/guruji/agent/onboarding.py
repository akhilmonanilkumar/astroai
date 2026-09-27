"""In-chat onboarding, enforced in code: consent → 18+ → name → date → time → place → confirm.

Consent and age are button-only and recorded with the button reply's wamid as proof.
Birth details may come one at a time or all in one message; each answer is extracted,
validated and merged, and the next missing detail is asked for. Nothing is charted
until the user confirms the summary.

Pure logic over a plaintext `Draft`; the graph encrypts it before it is stored.
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel

from guruji.agent.copy import BUTTONS, LINES, MONTHS_HI
from guruji.agent.extract import BirthFields, Field, clean_place, extract
from guruji.agent.language import Language
from guruji.db.models import Consent, UserState
from guruji.geo.places import Place, PlaceIndex
from guruji.turn.reply import Button, Reply
from guruji.whatsapp.models import IncomingMessage

Step = Literal["consent", "age", "collect", "place_choice", "confirm", "fix", "declined", "done"]
_MAX_PLACE_OPTIONS = 2  # + "None of these" = 3 buttons
_CLEAR_WINNER_RATIO = 20
_YES = frozenset(
    "yes y yeah yep ok okay correct right haan han ha haa ji sahi theek thik "
    "हाँ हां जी सही ठीक".split()
)


def _clear_winner(matches: list[Place]) -> bool:
    """One match, or one far bigger than the next (Bombay: Mumbai, not Bombay NZ)."""
    return len(matches) == 1 or matches[0].population >= _CLEAR_WINNER_RATIO * max(
        matches[1].population, 1
    )


def _is_yes(text: str) -> bool:
    words = text.casefold().replace(",", " ").replace("!", " ").replace(".", " ").split()
    return 0 < len(words) <= 3 and all(w in _YES or w == "hai" or w == "है" for w in words)


@dataclass
class PlaceChoice:
    label: str
    latitude: float
    longitude: float
    tz_name: str

    @classmethod
    def of(cls, p: Place) -> "PlaceChoice":
        return cls(p.label, p.latitude, p.longitude, p.tz_name)


@dataclass
class Draft:
    step: Step | None = None
    name: str | None = None
    date: str | None = None  # ISO
    time: str | None = None  # HH:MM
    time_unknown: bool = False
    place: PlaceChoice | None = None
    place_query: str | None = None  # offered early; resolved when the place is due
    candidates: list[PlaceChoice] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Draft":
        d = dict(data)
        if d.get("place"):
            d["place"] = PlaceChoice(**d["place"])
        d["candidates"] = [PlaceChoice(**c) for c in d.get("candidates", [])]
        return cls(**d)

    @property
    def fields(self) -> BirthFields:
        return BirthFields(
            name=self.name,
            date=date.fromisoformat(self.date) if self.date else None,
            time=time.fromisoformat(self.time) if self.time else None,
            time_unknown=self.time_unknown,
        )

    def missing(self) -> Field | None:
        if not self.name:
            return "name"
        if not self.date:
            return "date"
        if not self.time and not self.time_unknown:
            return "time"
        if not self.place:
            return "place"
        return None


@dataclass
class OnboardingResult:
    reply: Reply
    state: UserState
    draft: Draft
    consents: list[Consent] = field(default_factory=list)
    complete: bool = False  # confirmed: cast the chart and give the first reading


@dataclass(frozen=True)
class OnboardingDeps:
    places: PlaceIndex | None
    extractor: BaseChatModel | None
    privacy_url: str
    notice_version: str
    today: date


def _line(key: str, lang: Language, **kw: str) -> str:
    return LINES[key][lang].format(**kw)


def _btn(key: str, lang: Language) -> Button:
    return Button(key, BUTTONS[key][lang])


def format_date(iso: str, lang: Language) -> str:
    d = date.fromisoformat(iso)
    month = MONTHS_HI[d.month - 1] if lang == "hi" else d.strftime("%B")
    return f"{d.day} {month} {d.year}"


def format_time(hhmm: str) -> str:
    t = time.fromisoformat(hhmm)
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


class Onboarding:
    def __init__(self, deps: OnboardingDeps) -> None:
        self.deps = deps

    async def turn(
        self,
        state: UserState,
        draft: Draft,
        messages: list[IncomingMessage],
        lang: Language,
    ) -> OnboardingResult:
        last = messages[-1]
        reply_id = next((m.reply_id for m in reversed(messages) if m.reply_id), None)
        text = "\n".join(m.text for m in messages if m.kind == "text" and m.text).strip()

        if draft.step is None:
            return self._greet(state, draft, lang)
        if draft.step == "consent":
            return self._consent(state, draft, lang, reply_id, last)
        if draft.step == "age":
            return self._age(state, draft, lang, reply_id, last)
        if draft.step == "declined":
            return OnboardingResult(Reply([_line("underage", lang)]), "blocked", draft)

        if not text and not reply_id:
            return self._ask_next(state, draft, lang, prefix=_line("text_only", lang))
        if draft.step == "place_choice":
            return await self._place_choice(state, draft, lang, reply_id, text)
        if draft.step == "confirm":
            if reply_id == "confirm_yes" or _is_yes(text):
                draft.step = "done"
                return OnboardingResult(Reply([]), state, draft, complete=True)
            if reply_id == "confirm_change" or not text:
                draft.step = "fix"
                return OnboardingResult(Reply([_line("fix", lang)]), state, draft)
            return await self._collect(state, draft, lang, text, asking=None)
        if draft.step == "fix":
            return await self._collect(state, draft, lang, text, asking=None)
        # collect
        if reply_id == "time_unknown":
            draft.time, draft.time_unknown = None, True
            return self._ask_next(state, draft, lang)
        return await self._collect(state, draft, lang, text, asking=draft.missing())

    # --- consent and age ------------------------------------------------------------

    def _consent_buttons(self, lang: Language) -> list[Button]:
        return [_btn("consent_yes", lang), _btn("consent_notice", lang)]

    def _greet(self, state: UserState, draft: Draft, lang: Language) -> OnboardingResult:
        draft.step = "consent"
        return OnboardingResult(
            Reply(
                [_line("greet", lang), _line("consent", lang, url=self.deps.privacy_url)],
                self._consent_buttons(lang),
            ),
            state,
            draft,
        )

    def _consent(
        self,
        state: UserState,
        draft: Draft,
        lang: Language,
        reply_id: str | None,
        last: IncomingMessage,
    ) -> OnboardingResult:
        if reply_id == "consent_yes":
            draft.step = "age"
            consent = self._consent_row(last, age_confirmed=False)
            return OnboardingResult(
                Reply([_line("age", lang)], [_btn("age_yes", lang), _btn("age_no", lang)]),
                "consented",
                draft,
                [consent],
            )
        key = "notice" if reply_id == "consent_notice" else "tap_button"
        return OnboardingResult(
            Reply([_line(key, lang, url=self.deps.privacy_url)], self._consent_buttons(lang)),
            state,
            draft,
        )

    def _age(
        self,
        state: UserState,
        draft: Draft,
        lang: Language,
        reply_id: str | None,
        last: IncomingMessage,
    ) -> OnboardingResult:
        if reply_id == "age_yes":
            draft.step = "collect"
            consent = self._consent_row(last, age_confirmed=True)
            return OnboardingResult(
                Reply([_line("ask_name", lang)]), "onboarding", draft, [consent]
            )
        if reply_id == "age_no":
            draft.step = "declined"
            return OnboardingResult(Reply([_line("underage", lang)]), "blocked", draft)
        return OnboardingResult(
            Reply([_line("tap_button", lang)], [_btn("age_yes", lang), _btn("age_no", lang)]),
            state,
            draft,
        )

    def _consent_row(self, msg: IncomingMessage, *, age_confirmed: bool) -> Consent:
        return Consent(
            notice_version=self.deps.notice_version,
            purpose="readings",
            granted=True,
            age_confirmed=age_confirmed,
            wamid=msg.wamid,
            given_at=datetime.fromtimestamp(msg.ts, UTC),
        )

    # --- birth details ----------------------------------------------------------------

    async def _collect(
        self, state: UserState, draft: Draft, lang: Language, text: str, asking: Field | None
    ) -> OnboardingResult:
        found = await extract(text, asking, self.deps.today, self.deps.extractor)
        merged = found.merged_over(draft.fields)
        draft.name = merged.name
        draft.date = merged.date.isoformat() if merged.date else None
        draft.time = merged.time.strftime("%H:%M") if merged.time else None
        draft.time_unknown = merged.time_unknown
        if found.place is None and asking == "place" and not (found.date or found.time):
            found.place = clean_place(text)
        if found.place is None and found.place_guess and self._findable(found.place_guess):
            found.place = found.place_guess
        if found.place:
            if draft.missing() in ("name", "date", "time"):
                # Remember the place but finish the earlier questions first.
                self._remember_place(draft, found.place)
                return self._ask_next(state, draft, lang)
            return self._resolve_place(state, draft, lang, found.place)
        progressed = any(
            getattr(found, f) not in (None, False) for f in ("name", "date", "time", "time_unknown")
        )
        prefix = None if progressed else _line("didnt_get", lang)
        return self._ask_next(state, draft, lang, prefix=prefix)

    def _findable(self, query: str) -> bool:
        return bool(self.deps.places and self.deps.places.search(query, limit=1))

    def _remember_place(self, draft: Draft, query: str) -> None:
        matches = self.deps.places.search(query, limit=2) if self.deps.places else []
        if matches and _clear_winner(matches):
            draft.place, draft.place_query = PlaceChoice.of(matches[0]), None
        else:
            draft.place_query = query

    def _resolve_place(
        self, state: UserState, draft: Draft, lang: Language, query: str
    ) -> OnboardingResult:
        matches = (
            self.deps.places.search(query, limit=_MAX_PLACE_OPTIONS) if self.deps.places else []
        )
        if not matches:
            draft.place = None
            return OnboardingResult(
                Reply([_line("place_not_found", lang, query=query[:60])]), state, draft
            )
        if _clear_winner(matches):
            draft.place, draft.candidates = PlaceChoice.of(matches[0]), []
            return self._ask_next(state, draft, lang)
        draft.candidates = [PlaceChoice.of(p) for p in matches]
        draft.step = "place_choice"
        options = "\n".join(f"{i + 1}. {c.label}" for i, c in enumerate(draft.candidates))
        buttons = [_btn(f"place_{i}", lang) for i in range(len(draft.candidates))]
        return OnboardingResult(
            Reply(
                [_line("place_choose", lang, options=options)], [*buttons, _btn("place_none", lang)]
            ),
            state,
            draft,
        )

    async def _place_choice(
        self, state: UserState, draft: Draft, lang: Language, reply_id: str | None, text: str
    ) -> OnboardingResult:
        index: int | None = None
        if reply_id and reply_id.startswith("place_") and reply_id[6:].isdigit():
            index = int(reply_id[6:])
        elif text.strip().isdigit():
            index = int(text.strip()) - 1
        if index is not None and 0 <= index < len(draft.candidates):
            draft.place, draft.candidates = draft.candidates[index], []
            draft.step = "collect"
            return self._ask_next(state, draft, lang)
        draft.candidates, draft.place, draft.step = [], None, "collect"
        if reply_id == "place_none" or not text:
            return OnboardingResult(Reply([_line("ask_place", lang)]), state, draft)
        return await self._collect(state, draft, lang, text, asking="place")

    def _ask_next(
        self, state: UserState, draft: Draft, lang: Language, prefix: str | None = None
    ) -> OnboardingResult:
        missing = draft.missing()
        if missing == "place" and draft.place_query:
            query, draft.place_query = draft.place_query, None
            return self._resolve_place(state, draft, lang, query)
        buttons: list[Button] = []
        if missing is None:
            draft.step = "confirm"
            assert draft.date is not None and draft.place is not None
            question = _line(
                "confirm",
                lang,
                name=draft.name or "",
                date=format_date(draft.date, lang),
                time=format_time(draft.time) if draft.time else _line("time_unknown", lang),
                place=draft.place.label,
            )
            buttons = [_btn("confirm_yes", lang), _btn("confirm_change", lang)]
        else:
            draft.step = "collect"
            question = _line(f"ask_{missing}", lang, name=(draft.name or "").split(" ")[0])
            if missing == "time":
                buttons = [_btn("time_unknown", lang)]
        bubbles = [f"{prefix} {question}" if prefix else question]
        return OnboardingResult(Reply(bubbles, buttons), state, draft)
