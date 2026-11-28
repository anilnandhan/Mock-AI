import json
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .config import settings
from .db import engine, get_db, init_db
from .interviewer import TurnEvent, elapsed_seconds, run_turn
from .models import InterviewSession, Message, Report, utcnow
from .problems import PublicProblem, all_problems, get_problem, to_public
from .report import ReportError, generate_report

from . import tts
from .logging_setup import setup_logging

setup_logging()
log = logging.getLogger("app.api")
client_log = logging.getLogger("client")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    all_problems()  # fail fast on an invalid problem file
    tts.start()  # loads Alex's voice in the background
    yield


app = FastAPI(title="Mock-AI", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# One turn at a time per session.
_session_locks: dict[str, asyncio.Lock] = {}


class ProblemSummary(BaseModel):
    id: str
    title: str
    topic: str
    difficulty: str


class CreateSessionIn(BaseModel):
    problem_id: str


class TurnIn(BaseModel):
    event: TurnEvent
    text: str = ""
    code: str | None = None
    # The candidate kept talking or talked over the reply, so they did not hear it.
    interrupted: bool = False


class ClientLogEntry(BaseModel):
    t: str  # browser clock, HH:MM:SS.mmm
    level: str = "info"
    msg: str
    data: dict | None = None


class ClientLogIn(BaseModel):
    session_id: str | None = None
    entries: list[ClientLogEntry]


class TranscriptEntry(BaseModel):
    kind: str
    text: str
    elapsed_seconds: int
    phase: str


class SessionOut(BaseModel):
    id: str
    problem: PublicProblem
    phase: str
    status: str
    hint_level_max: int
    elapsed_seconds: int
    interview_minutes: int
    transcript: list[TranscriptEntry]
    code: str
    has_report: bool


def _load(db: Session, session_id: str) -> tuple[InterviewSession, object]:
    session = db.get(InterviewSession, session_id)
    if not session:
        raise HTTPException(404, "Interview not found")
    problem = get_problem(session.problem_id)
    if not problem:
        raise HTTPException(500, "Problem file for this interview is missing")
    return session, problem


def _session_out(db: Session, session: InterviewSession, problem) -> SessionOut:
    rows = db.exec(select(Message).where(Message.session_id == session.id).order_by(Message.id)).all()
    transcript = [
        TranscriptEntry(kind=m.kind, text=m.display_text, elapsed_seconds=m.elapsed_seconds, phase=m.phase)
        for m in rows
        if m.kind in ("candidate", "ai") and m.display_text
    ]
    return SessionOut(
        id=session.id,
        problem=to_public(problem),
        phase=session.phase,
        status=session.status,
        hint_level_max=session.hint_level_max,
        elapsed_seconds=elapsed_seconds(session),
        interview_minutes=settings.interview_minutes,
        transcript=transcript,
        code=session.last_code_sent if session.last_code_sent is not None else problem.starter_code,
        has_report=db.get(Report, session.id) is not None,
    )


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "problems": len(all_problems()), "provider": settings.llm_provider, "voice": tts.status()}


class SpeakIn(BaseModel):
    text: str


@app.post("/api/tts")
def speak(body: SpeakIn):
    """One sentence of Alex's speech as WAV audio. 503 means: use the browser voice."""
    text = body.text.strip()[:1000]
    if not text:
        raise HTTPException(400, "Nothing to say.")
    try:
        audio = tts.synthesize(text)
    except tts.VoiceNotReady as e:
        raise HTTPException(503, f"The voice is not ready ({e}).")
    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.get("/api/problems", response_model=list[ProblemSummary])
def list_problems():
    return [ProblemSummary(**p.model_dump(include=set(ProblemSummary.model_fields))) for p in all_problems().values()]


@app.post("/api/sessions", response_model=SessionOut)
def create_session(body: CreateSessionIn, db: Session = Depends(get_db)):
    problem = get_problem(body.problem_id)
    if not problem:
        raise HTTPException(404, "Problem not found")
    session = InterviewSession(problem_id=problem.id, provider=settings.llm_provider)
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_out(db, session, problem)


@app.get("/api/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: str, db: Session = Depends(get_db)):
    session, problem = _load(db, session_id)
    return _session_out(db, session, problem)


@app.post("/api/sessions/{session_id}/turn")
async def turn(session_id: str, body: TurnIn, db: Session = Depends(get_db)):
    session, problem = _load(db, session_id)
    sid = session_id[:8]

    def reject(reason: str):
        log.warning("[%s] turn rejected (event=%s): %s", sid, body.event, reason)
        raise HTTPException(409, reason)

    if session.status != "active":
        reject("This interview has ended")
    if body.event == "start" and db.exec(select(Message).where(Message.session_id == session_id)).first():
        reject("This interview has already started")

    lock = _session_locks.setdefault(session_id, asyncio.Lock())
    if lock.locked():
        reject("The interviewer is still responding")

    async def stream():
        # The request-scoped db session may close before streaming finishes,
        # so the turn uses its own.
        async with lock:
            with Session(engine) as turn_db:
                turn_session = turn_db.get(InterviewSession, session_id)
                async for chunk in run_turn(
                    turn_db, turn_session, problem, body.event, body.text, body.code, body.interrupted
                ):
                    yield chunk

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sessions/{session_id}/report")
async def report(session_id: str, db: Session = Depends(get_db)):
    session, problem = _load(db, session_id)
    if session.status == "active":
        session.status = "ended"
        session.ended_at = utcnow()
        db.add(session)
        db.commit()
    try:
        return await generate_report(db, session, problem)
    except ReportError as e:
        raise HTTPException(422, str(e))


@app.post("/api/client-log", status_code=204)
def client_log_route(body: ClientLogIn):
    """Browser-side events (mic, speech, turn-taking), written to the same log file."""
    sid = (body.session_id or "-")[:8]
    for e in body.entries[:200]:
        level = {"error": logging.ERROR, "warn": logging.WARNING, "debug": logging.DEBUG}.get(e.level, logging.INFO)
        extra = f" {json.dumps(e.data, ensure_ascii=False)[:500]}" if e.data else ""
        client_log.log(level, "[%s] %s %s%s", sid, e.t, e.msg, extra)
