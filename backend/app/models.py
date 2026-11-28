from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InterviewSession(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    problem_id: str
    # AI provider for this interview. It never changes mid-interview, because
    # the stored conversation is in this provider's message format.
    provider: str = "anthropic"
    phase: str = "intro"
    phase_started_at: datetime = Field(default_factory=utcnow)
    hint_level_max: int = 0
    status: str = "active"  # active | ended
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    # JSON list of tool_use ids from the last AI turn that still need a
    # tool_result in the next user message.
    pending_tool_use_ids: str = "[]"
    # The last code snapshot sent to the AI, to avoid resending unchanged code.
    last_code_sent: str | None = None


class Message(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, foreign_key="interviewsession.id")
    # "user" or "assistant".
    role: str
    # Exactly what was sent to / received from the provider, as JSON, in that
    # provider's format. The conversation is rebuilt from these verbatim every
    # turn (append-only).
    content_json: str
    # For AI turns: the interview state the AI reported (phase, hint, note).
    meta_json: str | None = None
    # candidate | event | ai
    kind: str
    # Human-readable text for the transcript ("" when the AI stayed silent).
    display_text: str = ""
    code_snapshot: str | None = None
    phase: str
    elapsed_seconds: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class Report(SQLModel, table=True):
    session_id: str = Field(primary_key=True, foreign_key="interviewsession.id")
    data_json: str
    created_at: datetime = Field(default_factory=utcnow)
