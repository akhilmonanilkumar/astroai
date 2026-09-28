"""Model bake-off and regression evals for the guru.

    python -m guruji eval --models sarvam:sarvam-105b,anthropic:<id> [--judge MODEL]

Every case in cases.toml is answered by each model through the real guru agent (same
prompt, tools, review loop and rule cards as production), then scored:

- accuracy: did the first draft state wrong chart facts (guruji.agent.verify), how many
  fact rewrites were needed, and is anything still wrong at the end;
- style: persona-breaking phrases, guardrail rules, markdown, more than two bubbles, length;
- language: does the reply mirror the user's language and script;
- latency;
- naturalness (with --judge): a judge model rates each reply the way a user would.

Reports go to tests/evals/output/ (gitignored; the charts are made up, but replies are
still model output worth keeping out of git).
"""

import asyncio
import json
import logging
import re
import statistics
import time
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from datetime import time as dtime
from importlib import resources
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, Field, ValidationError, field_validator

from guruji.agent import llm
from guruji.agent.guru import GuruContext, build_guru, message_text, run_guru, style_problems
from guruji.agent.language import Language, detect
from guruji.agent.verify import check_reply
from guruji.astro import BirthInput, Dossier, Sky, compute_dossier, transit_snapshot
from guruji.config import Settings
from guruji.geo.tz import birth_moment
from guruji.rag.factors import current_factors, natal_factors
from guruji.rag.retrieve import Retriever

log = logging.getLogger(__name__)

DEFAULT_OUT = Path("tests/evals/output")


@dataclass(frozen=True)
class ChartSpec:
    id: str
    name: str
    date: str
    latitude: float
    longitude: float
    tz: str
    time: str | None = None


@dataclass(frozen=True)
class Case:
    id: str
    chart: str
    language: Language
    kind: str
    text: str
    history: list[tuple[str, str]] = field(default_factory=list)


def load_cases(path: Path | None = None) -> tuple[dict[str, ChartSpec], list[Case]]:
    raw = (
        path.read_text("utf-8")
        if path
        else resources.files("guruji.evals").joinpath("cases.toml").read_text("utf-8")
    )
    data = tomllib.loads(raw)
    charts = {c["id"]: ChartSpec(**c) for c in data["chart"]}
    cases = [
        Case(**{**c, "history": [tuple(t) for t in c.get("history", [])]}) for c in data["case"]
    ]
    unknown = {c.chart for c in cases} - charts.keys()
    if unknown:
        raise ValueError(f"cases refer to unknown charts: {sorted(unknown)}")
    return charts, cases


def dossier_for(sky: Sky, spec: ChartSpec, now: datetime) -> Dossier:
    at = dtime.fromisoformat(spec.time) if spec.time else None
    birth = BirthInput(
        moment=birth_moment(date.fromisoformat(spec.date), at, spec.tz),
        latitude=spec.latitude,
        longitude=spec.longitude,
        time_known=at is not None,
    )
    return compute_dossier(sky, birth, now)


# --- judge ---------------------------------------------------------------------------


class Verdict(BaseModel):
    natural: int = Field(ge=1, le=5, description="sounds like a real person chatting")
    warmth: int = Field(ge=1, le=5)
    specific: int = Field(ge=1, le=5, description="answers this person, not generic")
    real_astrologer: bool = Field(description="would a user believe a human jyotishi wrote it")
    notes: str = ""

    @field_validator("real_astrologer", mode="before")
    @classmethod
    def _score_as_bool(cls, v: Any) -> Any:
        # Some judges rate this 1-5 like the other keys; 4+ means believable.
        return v >= 4 if isinstance(v, int | float) and not isinstance(v, bool) else v


_JUDGE_PROMPT = """You review replies from "Guruji", an AI Vedic astrologer that chats on
WhatsApp with people in India. Judge the reply the way the user would receive it.

The user writes in: {language}
Earlier conversation:
{history}
User's message: {text}

Guruji's reply (WhatsApp bubbles separated by ---):
{reply}

Rate 1-5: natural (reads like a real person texting, not a bot or an essay), warmth,
specific (answers this person's question rather than generic astrology). real_astrologer:
would a typical user believe a human jyotishi wrote this? Do not judge astrological
correctness. Reply with only a JSON object with keys natural, warmth, specific
(integers 1-5), real_astrologer (true or false), notes (one short sentence)."""


async def judge(model: BaseChatModel, case: Case, bubbles: list[str]) -> Verdict | None:
    history = "\n".join(f"{who}: {text}" for who, text in case.history) or "(none)"
    prompt = _JUDGE_PROMPT.format(
        language=case.language,
        history=history,
        text=case.text,
        reply="\n---\n".join(bubbles),
    )
    try:
        out = await model.ainvoke(prompt)
        match = re.search(r"\{.*\}", message_text(out), re.DOTALL)
        return Verdict.model_validate_json(match.group(0)) if match else None
    except (ValidationError, ValueError) as e:
        log.warning("judge output unusable for %s: %s", case.id, type(e).__name__)
    except Exception as e:  # a flaky judge must not sink the run
        log.warning("judge failed for %s: %s", case.id, type(e).__name__)
    return None


# --- one answer ------------------------------------------------------------------------


@dataclass
class Answer:
    model: str
    case: str
    kind: str
    language: str
    bubbles: list[str] = field(default_factory=list)
    seconds: float = 0.0
    fact_rewrites: int = 0
    still_wrong: list[str] = field(default_factory=list)
    style: list[str] = field(default_factory=list)
    language_ok: bool = False
    error: str | None = None
    verdict: dict[str, Any] | None = None

    @property
    def draft_right(self) -> bool:
        return self.error is None and self.fact_rewrites == 0

    @property
    def final_right(self) -> bool:
        return self.error is None and not self.still_wrong


def _history(case: Case) -> list[BaseMessage]:
    return [HumanMessage(t) if who == "user" else AIMessage(t) for who, t in case.history]


async def answer_case(
    model_name: str,
    agent: Any,
    case: Case,
    spec: ChartSpec,
    dossier: Dossier,
    sky: Sky,
    now: datetime,
    retriever: Retriever | None,
) -> Answer:
    out = Answer(model_name, case.id, case.kind, case.language)
    transits = transit_snapshot(sky, dossier.d1, now)
    factors = natal_factors(dossier) | current_factors(dossier, now, transits)
    cards = await retriever.for_question(case.text, factors) if retriever else []
    ctx = GuruContext(
        sky=sky,
        dossier=dossier,
        now=now,
        name=spec.name,
        facts=[],
        readings=[],
        language=case.language,
        transits=transits,
        cards=cards,
        factors=factors,
        retriever=retriever,
    )
    started = time.perf_counter()
    try:
        out.bubbles = await run_guru(agent, _history(case), case.text, ctx)
    except Exception as e:
        out.error = f"{type(e).__name__}: {str(e)[:200]}"
        return out
    finally:
        out.seconds = round(time.perf_counter() - started, 2)
    text = "\n\n".join(out.bubbles)
    out.fact_rewrites = ctx.fact_rewrites
    out.still_wrong = [p.said for p in check_reply(text, dossier, now, sky=sky, transits=transits)]
    out.style = style_problems(out.bubbles)
    found = detect(text)
    out.language_ok = found == case.language or (found is None and case.language == "en")
    return out


# --- a run -----------------------------------------------------------------------------


def require_model(name: str, settings: Settings) -> BaseChatModel:
    """The real model; never silently the fake one (unless "fake" was asked for)."""
    if name != llm.FAKE and llm.resolve(name, settings) == llm.FAKE:
        provider = name.partition(":")[0]
        raise SystemExit(f"no API key for {provider!r} (set {provider.upper()}_API_KEY)")
    return llm.make_model(name, settings, reading=True)


async def run_eval(
    settings: Settings,
    sky: Sky,
    models: dict[str, BaseChatModel],
    *,
    judge_model: BaseChatModel | None = None,
    retriever: Retriever | None = None,
    only: set[str] | None = None,
    limit: int | None = None,
    concurrency: int = 4,
    now: datetime | None = None,
    cases_path: Path | None = None,
) -> list[Answer]:
    now = now or datetime.now(UTC)
    charts, cases = load_cases(cases_path)
    if only:
        cases = [c for c in cases if c.id in only]
    cases = cases[:limit] if limit else cases
    dossiers = {
        cid: await asyncio.to_thread(dossier_for, sky, spec, now) for cid, spec in charts.items()
    }
    slots = asyncio.Semaphore(concurrency)
    answers: list[Answer] = []

    async def one(name: str, agent: Any, case: Case) -> None:
        async with slots:
            spec = charts[case.chart]
            a = await answer_case(
                name, agent, case, spec, dossiers[case.chart], sky, now, retriever
            )
            if judge_model is not None and a.error is None and a.bubbles:
                v = await judge(judge_model, case, a.bubbles)
                a.verdict = v.model_dump() if v else None
            answers.append(a)
            log.info(
                "%s %s %.1fs rewrites=%d wrong=%d",
                name,
                case.id,
                a.seconds,
                a.fact_rewrites,
                len(a.still_wrong),
            )

    for name, model in models.items():
        agent = build_guru([model], cache_blocks=llm.is_anthropic(name))
        await asyncio.gather(*(one(name, agent, c) for c in cases))
    order = {c.id: i for i, c in enumerate(cases)}
    return sorted(answers, key=lambda a: (list(models).index(a.model), order[a.case]))


def _pct(n: int, d: int) -> str:
    return f"{round(100 * n / d)}%" if d else "—"


def summary(answers: list[Answer]) -> str:
    lines = [
        "| model | cases | errors | draft facts right | final facts right | avg rewrites "
        "| style ok | language ok | median s | p90 s | natural | believable |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in dict.fromkeys(a.model for a in answers):
        mine = [a for a in answers if a.model == name]
        ok = [a for a in mine if a.error is None]
        secs = sorted(a.seconds for a in ok) or [0.0]
        verdicts = [a.verdict for a in ok if a.verdict]
        natural = f"{statistics.mean(v['natural'] for v in verdicts):.1f}/5" if verdicts else "—"
        believable = _pct(sum(bool(v["real_astrologer"]) for v in verdicts), len(verdicts))
        lines.append(
            f"| {name} | {len(mine)} | {len(mine) - len(ok)} "
            f"| {_pct(sum(a.draft_right for a in ok), len(ok))} "
            f"| {_pct(sum(a.final_right for a in ok), len(ok))} "
            f"| {statistics.mean(a.fact_rewrites for a in ok) if ok else 0:.2f} "
            f"| {_pct(sum(not a.style for a in ok), len(ok))} "
            f"| {_pct(sum(a.language_ok for a in ok), len(ok))} "
            f"| {statistics.median(secs):.1f} | {secs[int(0.9 * (len(secs) - 1))]:.1f} "
            f"| {natural} | {believable if verdicts else '—'} |"
        )
    problems = [a for a in answers if a.error or a.still_wrong or a.style or not a.language_ok]
    if problems:
        lines += ["", "Cases needing a look:", ""]
        for a in problems:
            why = a.error or "; ".join(
                [
                    *(f"wrong: {w}" for w in a.still_wrong),
                    *a.style,
                    *([] if a.language_ok else [f"not in {a.language}"]),
                ]
            )
            lines.append(f"- {a.model} / {a.case}: {why}")
    return "\n".join(lines)


def write_report(answers: list[Answer], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    (out_dir / f"eval-{stamp}.json").write_text(
        json.dumps([asdict(a) for a in answers], ensure_ascii=False, indent=1), "utf-8"
    )
    md = out_dir / f"eval-{stamp}.md"
    body = [f"# Guru eval {stamp}", "", summary(answers), "", "## Replies", ""]
    for a in answers:
        body += [f"### {a.model} / {a.case}", "", *(f"> {b}" for b in a.bubbles), ""]
    md.write_text("\n".join(body), "utf-8")
    return md
