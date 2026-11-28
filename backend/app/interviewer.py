"""Runs one interviewer turn: builds the request, streams Claude's reply, saves state.

The Claude conversation is append-only: every turn appends one user message
(turn context + code + what the candidate said) and one assistant message
(stored verbatim, including thinking and tool_use blocks). Nothing earlier is
ever edited, which keeps prompt caching and preserved thinking valid.
"""

import json
import time
import logging
from collections.abc import AsyncIterator
from typing import Literal

from sqlmodel import Session, select

from .config import settings
from .models import InterviewSession, Message, utcnow
from .problems import Problem
from .providers import TurnOutcome, get_provider
from .prompts import (
    PHASE_TARGET_MINUTES,
    PHASES,
    UPDATE_INTERVIEW_TOOL,
    interviewer_system_prompt,
)

log = logging.getLogger(__name__)

TurnEvent = Literal["start", "message", "silence", "time_up", "end_early"]

FALLBACK_SPOKEN_ERROR = "Sorry, I lost my train of thought there. Could you say that again?"


def fmt_mmss(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def elapsed_seconds(session: InterviewSession) -> int:
    return max(0, int((utcnow() - session.started_at).total_seconds()))


def build_turn_text(
    session: InterviewSession,
    event: TurnEvent,
    text: str,
    code: str | None,
    interrupted: bool = False,
) -> str:
    """The text block appended for this turn. Pure function, easy to test."""
    elapsed = elapsed_seconds(session)
    total = settings.interview_minutes * 60
    phase_secs = max(0, int((utcnow() - session.phase_started_at).total_seconds()))
    target = PHASE_TARGET_MINUTES.get(session.phase, 5) * 60

    lines = [
        "<turn_context>",
        f"event: {event}",
        f"elapsed: {fmt_mmss(elapsed)} of {fmt_mmss(total)}",
        f"current phase: {session.phase} (running {fmt_mmss(phase_secs)}, target {fmt_mmss(target)})",
    ]
    if session.phase not in ("intro", "wrap_up") and phase_secs > target + 60:
        lines.append(f"note: The {session.phase} phase is over time. Steer the candidate forward.")
    if event != "time_up" and total - elapsed <= 5 * 60 and session.phase not in ("follow_up", "wrap_up"):
        lines.append("note: Less than 5 minutes left. Start moving toward wrap-up.")
    if interrupted:
        lines.append(
            "note: The candidate kept talking or interrupted you, so they did not hear your previous reply "
            "(or heard only its start)."
        )
    lines.append("</turn_context>")

    if code is not None:
        if code == session.last_code_sent:
            lines.append("<candidate_code>(unchanged since last turn)</candidate_code>")
        else:
            lines.append(f"<candidate_code>\n{code}\n</candidate_code>")

    if event == "message":
        lines.append(f"<candidate_said>\n{text.strip() or '(nothing audible)'}\n</candidate_said>")
    elif event == "start":
        lines.append("(The candidate has joined. Begin the interview.)")
    elif event == "silence":
        lines.append("(The candidate has been silent and not typing for a few minutes.)")
    elif event == "time_up":
        lines.append("(Time is up.)")
    elif event == "end_early":
        lines.append("(The candidate asked to end the interview now.)")
    return "\n".join(lines)


def load_history(db: Session, session_id: str) -> list[tuple[str, object]]:
    rows = db.exec(select(Message).where(Message.session_id == session_id).order_by(Message.id)).all()
    return [(m.role, json.loads(m.content_json)) for m in rows]


def _preview(text: str, n: int = 120) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n] + "…"


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _valid_state(args: object) -> dict | None:
    if not isinstance(args, dict):
        return None
    if args.get("phase") not in PHASES:
        return None
    if args.get("hint_level_given") not in (0, 1, 2, 3):
        return None
    if not isinstance(args.get("interview_over"), bool):
        return None
    if not isinstance(args.get("note"), str):
        return None
    return args


async def run_turn(
    db: Session,
    session: InterviewSession,
    problem: Problem,
    event: TurnEvent,
    text: str,
    code: str | None,
    interrupted: bool = False,
) -> AsyncIterator[str]:
    """Yields server-sent event strings for one interviewer turn."""
    provider = get_provider(session.provider)
    sid = session.id[:8]
    t0 = time.monotonic()
    log.info(
        "[%s] turn start event=%s phase=%s provider=%s said=%r code_changed=%s interrupted=%s",
        sid, event, session.phase, provider.name, _preview(text),
        code is not None and code != session.last_code_sent, interrupted,
    )

    # 1. Append the user message for this turn.
    entry = provider.user_entry(
        json.loads(session.pending_tool_use_ids), build_turn_text(session, event, text, code, interrupted)
    )
    elapsed = elapsed_seconds(session)
    user_msg = Message(
        session_id=session.id,
        role="user",
        content_json=json.dumps(entry),
        kind="candidate" if event == "message" else "event",
        display_text=text.strip() if event == "message" else f"[{event}]",
        code_snapshot=code,
        phase=session.phase,
        elapsed_seconds=elapsed,
    )
    db.add(user_msg)
    saved_pending, saved_code = session.pending_tool_use_ids, session.last_code_sent
    session.pending_tool_use_ids = "[]"
    if code is not None:
        session.last_code_sent = code
    db.add(session)
    db.commit()

    # 2. Stream the interviewer's reply.
    outcome: TurnOutcome | None = None
    first_text_at: float | None = None
    history = load_history(db, session.id)
    log.debug("[%s] sending %d history entries; new user entry: %s", sid, len(history), json.dumps(entry)[:4000])
    try:
        async for item in provider.stream_turn(
            interviewer_system_prompt(problem, settings.interview_minutes),
            history,
            UPDATE_INTERVIEW_TOOL,
        ):
            if isinstance(item, TurnOutcome):
                outcome = item
            elif item:
                if first_text_at is None:
                    first_text_at = time.monotonic()
                yield _sse({"type": "text", "text": item})
        if outcome is None:
            raise RuntimeError("Provider finished without a result")
    except Exception as exc:  # noqa: BLE001 - any failure must leave the session retryable
        log.exception("[%s] turn FAILED after %.1fs (%s): %s", sid, time.monotonic() - t0, provider.name, exc)
        # Undo this turn. The failed user message is the last one in the
        # conversation, so removing it never edits earlier history.
        db.delete(user_msg)
        session.pending_tool_use_ids, session.last_code_sent = saved_pending, saved_code
        db.add(session)
        db.commit()
        yield _sse({"type": "error", "message": provider.describe_error(exc)})
        return

    if outcome.status == "refusal":
        # Not saved, so the history stays valid; the next user message simply follows.
        log.warning("[%s] interviewer refusal; spoke the fallback line", sid)
        yield _sse({"type": "text", "text": FALLBACK_SPOKEN_ERROR})
        yield _sse({"type": "done"})
        return

    # 3. Save the assistant message verbatim and apply the state update.
    state = None
    if outcome.status == "ok":
        for call in outcome.tool_calls:
            if call.name == "update_interview":
                state = _valid_state(call.input)

    db.add(
        Message(
            session_id=session.id,
            role="assistant",
            content_json=json.dumps(outcome.stored),
            meta_json=json.dumps(state) if state else None,
            kind="ai",
            display_text=outcome.spoken,
            phase=session.phase,
            elapsed_seconds=elapsed_seconds(session),
        )
    )
    session.pending_tool_use_ids = json.dumps([c.id for c in outcome.tool_calls])
    if not state:
        log.warning(
            "[%s] no valid update_interview state this turn (tool calls: %s)",
            sid, [(c.name, c.input) for c in outcome.tool_calls],
        )
    phase_before = session.phase

    if state:
        if state["phase"] != session.phase:
            session.phase = state["phase"]
            session.phase_started_at = utcnow()
        session.hint_level_max = max(session.hint_level_max, state["hint_level_given"])
        if state["interview_over"]:
            session.status = "ended"
            session.ended_at = utcnow()
    if event in ("time_up", "end_early") and session.status != "ended":
        session.status = "ended"
        session.ended_at = utcnow()

    db.add(session)
    db.commit()

    total = time.monotonic() - t0
    log.info(
        "[%s] turn done in %.1fs (first words %s) status=%s spoke=%r phase=%s->%s hint=%s over=%s session=%s",
        sid, total,
        f"{first_text_at - t0:.1f}s" if first_text_at else "never",
        outcome.status, _preview(outcome.spoken), phase_before, session.phase,
        state and state["hint_level_given"], state and state["interview_over"], session.status,
    )
    if total > 8:
        log.warning("[%s] slow turn (%.1fs). Look above for '429 Too Many Requests' / 'Retrying request'.", sid, total)
    if state and state.get("note"):
        log.info("[%s] interviewer note: %s", sid, state["note"])

    yield _sse(
        {
            "type": "state",
            "phase": session.phase,
            "hint_level_max": session.hint_level_max,
            "status": session.status,
        }
    )
    yield _sse({"type": "done"})
