"""End-of-interview grading with the report model."""

import json
import logging

from sqlmodel import Session, select

from .interviewer import fmt_mmss
from .models import InterviewSession, Message, Report
from .problems import Problem
from .providers import ProviderError, get_provider
from .prompts import REPORT_SCHEMA, REPORT_SYSTEM_PROMPT

log = logging.getLogger(__name__)


class ReportError(Exception):
    pass


def build_report_input(session: InterviewSession, problem: Problem, rows: list[Message]) -> str:
    transcript_lines: list[str] = []
    final_code = problem.starter_code
    snapshots: list[tuple[int, str]] = []
    notes: list[str] = []
    for m in rows:
        t = fmt_mmss(m.elapsed_seconds)
        if m.kind == "ai":
            if m.display_text:
                transcript_lines.append(f"[{t}] Interviewer: {m.display_text}")
            meta = json.loads(m.meta_json) if m.meta_json else {}
            if meta.get("hint_level_given"):
                transcript_lines.append(f"[{t}] (interviewer gave a level {meta['hint_level_given']} hint)")
            if meta.get("note"):
                notes.append(f"[{t}] {meta['note']}")
        elif m.kind == "candidate":
            transcript_lines.append(f"[{t}] Candidate: {m.display_text or '(silence)'}")
        else:
            transcript_lines.append(f"[{t}] (event: {m.display_text.strip('[]')})")
        if m.code_snapshot is not None and (not snapshots or snapshots[-1][1] != m.code_snapshot):
            snapshots.append((m.elapsed_seconds, m.code_snapshot))
            final_code = m.code_snapshot

    # Show how the code evolved, keeping the request a sensible size.
    shown = snapshots[-6:-1]
    history = "\n\n".join(f"--- code at {fmt_mmss(s)} ---\n{c}" for s, c in shown) or "(no earlier snapshots)"

    private = {
        "title": problem.title,
        "statement": problem.statement,
        "constraints": problem.constraints,
        "reference_solution": problem.reference_solution,
        "optimal_complexity": problem.optimal_complexity,
        "acceptable_approaches": problem.acceptable_approaches,
        "tricky_cases": [c.model_dump() for c in problem.tricky_cases],
        "common_mistakes": problem.common_mistakes,
    }
    return f"""<problem>
{json.dumps(private, indent=2)}
</problem>

<interview_facts>
duration: {fmt_mmss(rows[-1].elapsed_seconds if rows else 0)}
phase reached: {session.phase}
highest hint level given: {session.hint_level_max}
</interview_facts>

<transcript>
{chr(10).join(transcript_lines)}
</transcript>

<interviewer_private_notes>
{chr(10).join(notes) or "(none)"}
</interviewer_private_notes>

<earlier_code_snapshots>
{history}
</earlier_code_snapshots>

<final_code>
{final_code}
</final_code>

Trace the final code on every tricky case, then write the evaluation."""


async def generate_report(db: Session, session: InterviewSession, problem: Problem) -> dict:
    existing = db.get(Report, session.id)
    if existing:
        return json.loads(existing.data_json)

    rows = list(db.exec(select(Message).where(Message.session_id == session.id).order_by(Message.id)).all())
    if not any(m.kind == "candidate" for m in rows):
        raise ReportError("The candidate did not say anything, so there is nothing to evaluate.")

    provider = get_provider(session.provider)
    try:
        data = await provider.generate_json(
            REPORT_SYSTEM_PROMPT, build_report_input(session, problem, rows), REPORT_SCHEMA, "interview_report"
        )
    except ProviderError as e:
        raise ReportError(str(e)) from e
    except json.JSONDecodeError as e:
        raise ReportError("The report came back malformed. Please try again.") from e
    except Exception as e:  # noqa: BLE001
        log.exception("Report generation failed (%s)", provider.name)
        raise ReportError(provider.describe_error(e)) from e

    db.add(Report(session_id=session.id, data_json=json.dumps(data)))
    db.commit()
    return data
