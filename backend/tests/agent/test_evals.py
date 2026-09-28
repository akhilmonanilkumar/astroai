"""The eval harness scores what it should (runs in CI with scripted models)."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.messages import AIMessage

from guruji.agent.llm import ScriptedChatModel
from guruji.astro import Sky
from guruji.astro.constants import GRAHAS
from guruji.astro.dasha import dasha_at
from guruji.config import Settings
from guruji.evals.runner import dossier_for, load_cases, run_eval, summary, write_report

NOW = datetime(2026, 9, 28, 6, tzinfo=UTC)


def test_cases_file_is_valid() -> None:
    charts, cases = load_cases()
    assert len(cases) >= 20 and len({c.id for c in cases}) == len(cases)
    assert {c.language for c in cases} == {"en", "hinglish", "hi"}
    assert any(spec.time is None for spec in charts.values())  # a no-birth-time chart


async def test_scores_accuracy_language_and_judge(
    settings: Settings, sky: Sky, tmp_path: Path
) -> None:
    charts, cases = load_cases()
    case = next(c for c in cases if c.id == "en-dasha-now")
    md, ad = dasha_at(dossier_for(sky, charts[case.chart], NOW).dasha.mahadashas, NOW)[:2]
    wrong = next(g for g in GRAHAS if g not in (md.lord, ad.lord))

    def replies() -> Iterator[AIMessage]:
        yield AIMessage(f"Right now your {wrong} antardasha is running.")  # wrong draft
        yield AIMessage(f"Right now your {ad.lord} antardasha is running, a steady time.")

    guru = ScriptedChatModel(messages=replies())
    judge = ScriptedChatModel(
        messages=iter(
            [
                AIMessage(
                    '{"natural": 4, "warmth": 4, "specific": 5, "real_astrologer": true, '
                    '"notes": "direct"}'
                )
            ]
        )
    )
    [a] = await run_eval(
        settings, sky, {"scripted": guru}, judge_model=judge, only={case.id}, now=NOW
    )
    assert a.error is None and a.fact_rewrites == 1
    assert not a.draft_right and a.final_right
    assert a.language_ok and a.style == []
    assert a.verdict is not None and a.verdict["natural"] == 4
    table = summary([a])
    assert "| scripted | 1 | 0 | 0% | 100% | 1.00 |" in table
    report = write_report([a], tmp_path)
    assert f"{ad.lord} antardasha" in report.read_text("utf-8")
