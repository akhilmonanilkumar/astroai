"""The guru: a LangChain `create_agent` reading from the user's chart dossier.

Prompt layout (Anthropic prompt caching is a prefix match, so stable parts go first):
  1. persona bible (same for everyone)                  -> cache breakpoint
  2. this user's chart (same every turn for this user)  -> cache breakpoint
  3. today: date, running dasha, transits, memory, turn instructions (changes every turn)

Tools are deterministic: timing questions go to the dasha and transit engine, never to
the model's own arithmetic. Memory tools only buffer writes in the turn context; the
graph commits them with the reply, once per turn.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from functools import cache
from importlib import resources
from typing import Any, cast
from zoneinfo import ZoneInfo

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRequest,
    after_model,
    dynamic_prompt,
)
from langchain.tools import ToolRuntime, tool
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from guruji.agent import render
from guruji.agent.verify import check_reply, feedback
from guruji.astro import Dossier, OutOfRangeError, Sky, TransitSnapshot, transit_snapshot
from guruji.astro.dasha import dasha_at, subperiods
from guruji.db.models import FACT_CATEGORIES, LifeFact, Reading
from guruji.rag.cards import Card
from guruji.rag.retrieve import Retriever
from guruji.safety.guard import violation

MAX_BUBBLES = 2
_MAX_TOOL_PERIODS = 40
_STYLE_MARKER = "[style check]"
_FACT_MARKER = "[fact check]"
# Rewrites asked for wrong chart facts; after these the graph drops the wrong sentences.
MAX_FACT_REWRITES = 2
_IST = ZoneInfo("Asia/Kolkata")
_SPOKEN_RULE = (
    "This reply will be converted to a voice note: write it to be heard. No emoji, symbols "
    "or lists; natural spoken sentences; at most 70 words (about 30 seconds)."
)
_BRIEF_RULE = (
    "This is their free answer for today: keep it short, one bubble of two or three "
    "sentences, with the single most useful point."
)
# Models tend to answer Hinglish in Devanagari; say the script explicitly every turn.
_LANGUAGE_RULE = {
    "en": "simple Indian English.",
    "hinglish": "Hinglish: Hindi written in Roman (Latin) letters, exactly as the user "
    "writes. Do not use Devanagari script.",
    "hi": "Hindi in Devanagari script.",
}

# Phrases that break the persona. Matching replies go back to the model once for a rewrite.
_BOT_PHRASES = re.compile(
    r"\bas an ai\b|\bas a language model\b|\bi am an ai language model\b|"
    r"\bi hope this helps\b|\bgreat question\b|^certainly!|\bfeel free to (ask|reach)\b|"
    r"\bi understand your concern\b|\bi'?m (a|an) (real )?(human|person)\b|"
    r"\bmain (ek )?insaan hoon\b",
    re.IGNORECASE | re.MULTILINE,
)
_MD_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_MD_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.MULTILINE)
_MD_BULLET = re.compile(r"^[ \t]*(?:[-*•]|\d+[.)])[ \t]+", re.MULTILINE)


@cache
def persona() -> str:
    return resources.files("guruji.agent").joinpath("persona/guruji.md").read_text("utf-8")


@dataclass
class GuruContext:
    """Per-turn runtime context (not persisted; tools buffer writes here)."""

    sky: Sky
    dossier: Dossier
    now: datetime
    name: str | None
    facts: list[LifeFact]
    readings: list[Reading]
    first_reading: bool = False
    language: str = "en"  # en | hinglish | hi, detected from what the user types
    spoken: bool = False  # the reply will be sent as a voice note
    brief: bool = False  # the daily free answer: keep it short
    transits: TransitSnapshot | None = None
    cards: list[Card] = field(default_factory=list)  # retrieved for this turn
    factors: set[str] = field(default_factory=set)  # this chart's factor keys
    retriever: Retriever | None = None
    new_facts: list[tuple[str, str]] = field(default_factory=list)
    new_readings: list[tuple[str, str, list[str]]] = field(default_factory=list)
    fact_rewrites: int = 0  # how often the fact check sent a reply back (for evals, logs)


# --- prompt ------------------------------------------------------------------------


def today_block(ctx: GuruContext) -> str:
    d = ctx.dossier
    lines = [
        "TODAY",
        f"Now: {ctx.now.astimezone(_IST):%A %d %B %Y, %H:%M} IST",
        f"User's name: {ctx.name or 'not given'}",
        f"Reply language: {_LANGUAGE_RULE.get(ctx.language, _LANGUAGE_RULE['en'])}",
        *([_SPOKEN_RULE] if ctx.spoken else []),
        *([_BRIEF_RULE] if ctx.brief else []),
        render.dasha_now(d, ctx.now),
    ]
    if ctx.transits is not None:
        lines.append(render.transits_block(ctx.transits, d.time_known))
    lines.append(render.memory_block(ctx.facts, ctx.readings))
    if ctx.cards:
        lines.append(render.cards_block(ctx.cards))
    # Last, where models weigh it most: the examples in the persona use made-up charts.
    lines.append(
        "FACTS RULE: name only placements, dashas, transits and dates that appear in the "
        "BIRTH CHART and TODAY sections above or in tool results. The persona examples use "
        "fictional charts; never reuse their details. For any timing question, call "
        "dasha_periods or transits_on before answering."
    )
    # Very last, so it outweighs the language of earlier turns in the history.
    lines.append(
        "LANGUAGE: write this reply in "
        f"{_LANGUAGE_RULE.get(ctx.language, _LANGUAGE_RULE['en'])} Match the user's latest "
        "message, even if earlier messages were in another language."
    )
    if ctx.first_reading:
        lines.append(
            "THIS TURN: they have just finished onboarding and this is their first reading. "
            "Give a warm, specific overview from the chart: the nature their lagna and Moon "
            "show, the theme of the dasha now running, and one real strength. Two bubbles, "
            "a little longer than usual. Do not re-introduce yourself as an AI. End by "
            "inviting them to ask about whatever is on their mind."
        )
    return "\n".join(lines)


def system_message(ctx: GuruContext, *, blocks: bool = True) -> SystemMessage:
    """blocks=True: Anthropic-style cached content blocks. False: one string, as
    OpenAI-compatible APIs (Sarvam) require; the stable parts still come first."""
    parts = [persona(), render.chart_block(ctx.dossier), today_block(ctx)]
    if not blocks:
        return SystemMessage(content="\n\n".join(parts))
    cached = {"type": "ephemeral"}
    return SystemMessage(
        content=[
            {"type": "text", "text": parts[0], "cache_control": cached},
            {"type": "text", "text": parts[1], "cache_control": cached},
            {"type": "text", "text": parts[2]},
        ]
    )


def _prompt_middleware(blocks: bool) -> AgentMiddleware[Any, Any]:
    @dynamic_prompt
    def guru_prompt(request: ModelRequest) -> SystemMessage:
        ctx = cast(GuruContext, request.runtime.context)
        return system_message(ctx, blocks=blocks)

    return guru_prompt


# --- style -------------------------------------------------------------------------


def _last_text(state: AgentState) -> AIMessage | None:
    msg = state["messages"][-1] if state["messages"] else None
    if isinstance(msg, AIMessage) and not msg.tool_calls:
        return msg
    return None


def _asked(state: AgentState, marker: str) -> int:
    return sum(
        1 for m in state["messages"] if isinstance(m, HumanMessage) and marker in str(m.content)
    )


def _style_instruction(text: str) -> str | None:
    rule = violation(text)
    if rule is not None:
        return (
            f"Your last reply breaks a rule ({rule}). Rewrite it as guidance without that "
            "claim: no predictions of death or lifespan, no diagnosis, no trading calls, no "
            "legal verdicts, no guarantees, no fear."
        )
    hit = _BOT_PHRASES.search(text)
    if hit is None:
        return None
    return (
        f'Rewrite your last reply with the same content but without "{hit.group(0)}". '
        "Stay in Guruji's voice; if asked, you may say you are an AI astrologer in "
        "natural words."
    )


@after_model(can_jump_to=["model"])
def review(state: AgentState, runtime: Runtime[GuruContext]) -> dict[str, Any] | None:
    """Send a finished reply back once for style or guardrails, and up to twice for chart
    facts that don't match the dossier (guruji.agent.verify)."""
    msg = _last_text(state)
    if msg is None:
        return None
    text = message_text(msg)
    if not _asked(state, _STYLE_MARKER):
        instruction = _style_instruction(text)
        if instruction is not None:
            note = f"{_STYLE_MARKER} {instruction} Reply only with the rewritten message."
            return {"messages": [HumanMessage(note)], "jump_to": "model"}
    ctx = runtime.context
    if _asked(state, _FACT_MARKER) >= MAX_FACT_REWRITES:
        return None
    problems = check_reply(text, ctx.dossier, ctx.now, sky=ctx.sky, transits=ctx.transits)
    if not problems:
        return None
    ctx.fact_rewrites += 1
    note = f"{_FACT_MARKER} {feedback(problems)} Reply only with the rewritten message."
    return {"messages": [HumanMessage(note)], "jump_to": "model"}


# A WhatsApp reply longer than this reads like an essay.
MAX_REPLY_CHARS = 900


def style_problems(bubbles: list[str]) -> list[str]:
    """What a finished reply gets wrong on style (for evals and logs)."""
    text = "\n\n".join(bubbles)
    out: list[str] = []
    if not bubbles:
        out.append("empty reply")
    if len(bubbles) > MAX_BUBBLES:
        out.append(f"{len(bubbles)} bubbles")
    if len(text) > MAX_REPLY_CHARS:
        out.append(f"too long ({len(text)} chars)")
    rule = violation(text)
    if rule is not None:
        out.append(f"guardrail: {rule}")
    hit = _BOT_PHRASES.search(text)
    if hit is not None:
        out.append(f'bot phrase "{hit.group(0)}"')
    return out


def message_text(msg: BaseMessage) -> str:
    if isinstance(msg.content, str):
        return msg.content
    return "".join(
        b.get("text", "") for b in msg.content if isinstance(b, dict) and b.get("type") == "text"
    )


def to_bubbles(text: str, max_bubbles: int = MAX_BUBBLES) -> list[str]:
    """Strip markdown and split into at most `max_bubbles` WhatsApp messages."""
    text = _MD_BOLD.sub(r"\2", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_BULLET.sub("", text)
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) > max_bubbles:
        paras = [*paras[: max_bubbles - 1], "\n\n".join(paras[max_bubbles - 1 :])]
    return paras


# --- tools -------------------------------------------------------------------------


def _parse_day(value: str) -> date:
    return date.fromisoformat(value.strip()[:10])


@tool
def dasha_periods(from_date: str, to_date: str, runtime: ToolRuntime[GuruContext]) -> str:
    """Vimshottari antardasha and pratyantardasha periods overlapping a date range.

    Use for any timing question ("when", "which months"). Dates are YYYY-MM-DD.
    """
    try:
        start, end = _parse_day(from_date), _parse_day(to_date)
    except ValueError:
        return "Dates must be YYYY-MM-DD."
    lo = datetime.combine(start, time(0), UTC)
    hi = datetime.combine(end, time(23, 59), UTC)
    lines: list[str] = []
    for md in runtime.context.dossier.dasha.mahadashas:
        for ad in md.sub:
            if ad.end < lo or ad.start > hi:
                continue
            lines.append(
                f"{md.lord} mahadasha / {ad.lord} antardasha: "
                f"{ad.start:%d %b %Y} to {ad.end:%d %b %Y}"
            )
            for pd in subperiods(ad):
                if pd.end >= lo and pd.start <= hi:
                    lines.append(
                        f"  {pd.lord} pratyantardasha: {pd.start:%d %b %Y} to {pd.end:%d %b %Y}"
                    )
            if len(lines) >= _MAX_TOOL_PERIODS:
                return "\n".join([*lines, "(range truncated; ask for a shorter range)"])
    return "\n".join(lines) or "No periods in that range."


@tool
def transits_on(on_date: str, runtime: ToolRuntime[GuruContext]) -> str:
    """Planet positions on a date (YYYY-MM-DD), with houses from the natal lagna and Moon.

    Use for questions about a specific future or past date, or when a transit (like
    Jupiter or Saturn changing sign) matters for timing.
    """
    try:
        when = datetime.combine(_parse_day(on_date), time(12), UTC)
        ctx = runtime.context
        snap = transit_snapshot(ctx.sky, ctx.dossier.d1, when)
    except (ValueError, OutOfRangeError):
        return "Give a valid date between 1850 and 2149, as YYYY-MM-DD."
    return render.transits_block(snap, ctx.dossier.time_known)


@tool
def dasha_on(on_date: str, runtime: ToolRuntime[GuruContext]) -> str:
    """Which mahadasha, antardasha and pratyantardasha run on a date (YYYY-MM-DD)."""
    try:
        when = datetime.combine(_parse_day(on_date), time(12), UTC)
    except ValueError:
        return "Dates must be YYYY-MM-DD."
    chain = dasha_at(runtime.context.dossier.dasha.mahadashas, when)
    if len(chain) == 2:
        chain.append(next(p for p in subperiods(chain[1]) if p.contains(when)))
    labels = ("mahadasha", "antardasha", "pratyantardasha")
    return (
        "; ".join(
            f"{p.lord} {labels[i]} ({p.start:%d %b %Y} to {p.end:%d %b %Y})"
            for i, p in enumerate(chain)
        )
        or "Outside the computed dasha cycle."
    )


@tool
def remember_fact(category: str, fact: str, runtime: ToolRuntime[GuruContext]) -> str:
    """Save a lasting fact the user shared about their life, as one short neutral sentence.

    category: one of career, relationship, family, health, education, finance,
    relocation, spiritual, other.
    """
    cat = category if category in FACT_CATEGORIES else "other"
    runtime.context.new_facts.append((cat, fact.strip()[:300]))
    return "Saved."


@tool
def record_reading(
    topic: str, summary: str, factors: list[str], runtime: ToolRuntime[GuruContext]
) -> str:
    """Log a substantive reading or prediction you are giving, so you stay consistent later.

    topic: a short label (career, marriage, health, finance, ...). summary: one sentence of
    what you told them, including any time window. factors: chart factors you used.
    """
    runtime.context.new_readings.append(
        (topic.strip()[:40], summary.strip()[:400], [f.strip()[:60] for f in factors[:8]])
    )
    return "Recorded."


@tool
async def search_rules(query: str, runtime: ToolRuntime[GuruContext]) -> str:
    """Look up traditional rule cards for this chart on a topic (e.g. "marriage timing",
    "career change", "Saturn transit"). Only cards matching this chart's placements, plus
    general method, are returned. Use when the cards already given don't cover the question.
    """
    ctx = runtime.context
    if ctx.retriever is None:
        return "Rule cards are unavailable right now; read from the chart directly."
    cards = await ctx.retriever.for_question(query, ctx.factors)
    seen = {c.id for c in ctx.cards}
    fresh = [c for c in cards if c.id not in seen]
    return render.cards_block(fresh) if fresh else "No further cards; read from the chart."


TOOLS = [dasha_periods, dasha_on, transits_on, search_rules, remember_fact, record_reading]


# --- agent -------------------------------------------------------------------------


def build_guru(models: list[BaseChatModel], *, cache_blocks: bool = True) -> Any:
    middleware: list[AgentMiddleware[Any, Any]] = [
        _prompt_middleware(cache_blocks),
        review,
        # tools (~2) + one style rewrite + two fact rewrites, with room to spare
        ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"),
    ]
    if len(models) > 1:
        middleware.append(ModelFallbackMiddleware(*models[1:]))
    return create_agent(models[0], tools=TOOLS, middleware=middleware, context_schema=GuruContext)


async def run_guru(
    agent: Any, history: list[BaseMessage], text: str, ctx: GuruContext
) -> list[str]:
    result = await agent.ainvoke({"messages": [*history, HumanMessage(text)]}, context=ctx)
    final = next(
        (m for m in reversed(result["messages"]) if isinstance(m, AIMessage) and not m.tool_calls),
        None,
    )
    return to_bubbles(message_text(final)) if final is not None else []
