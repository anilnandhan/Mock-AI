"""API tests with a fake Claude client (no network, no API key needed)."""

import json
from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaMessage
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app import db as db_module
from app import main
from app.providers import anthropic_provider
from app.problems import all_problems, load_problems
from app.config import settings


def make_message(text: str, state: dict | None, stop_reason: str = "tool_use", msg_id: str = "msg_1") -> BetaMessage:
    content: list[dict] = []
    if text:
        content.append({"type": "text", "text": text})
    if state is not None:
        content.append({"type": "tool_use", "id": f"toolu_{msg_id}", "name": "update_interview", "input": state})
    return BetaMessage.model_validate(
        {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }
    )


class FakeStream:
    def __init__(self, message: BetaMessage):
        self.message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for block in self.message.content:
                if block.type == "text":
                    for word in block.text.split(" "):
                        yield SimpleNamespace(type="text", text=word + " ")

        return gen()

    async def get_final_message(self):
        return self.message


class FakeClient:
    def __init__(self, replies: list[BetaMessage]):
        self.replies = replies
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs, default=str)))
        return FakeStream(self.replies.pop(0))


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "tts_enabled", False)  # never download or load the voice model

    def override_db():
        with Session(engine) as s:
            yield s

    main.app.dependency_overrides[main.get_db] = override_db
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def read_sse(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_problem_files_are_valid():
    problems = load_problems(settings.problems_dir)
    assert "best-time-to-buy-sell-stock" in problems


def test_problems_list_hides_private_fields(client):
    r = client.get("/api/problems")
    assert r.status_code == 200
    assert r.json()[0]["id"] == "best-time-to-buy-sell-stock"

    s = client.post("/api/sessions", json={"problem_id": "best-time-to-buy-sell-stock"}).json()
    assert "reference_solution" not in s["problem"]
    assert "hints" not in s["problem"]
    assert s["problem"]["starter_code"]


def test_full_turn_flow(client, monkeypatch):
    fake = FakeClient(
        [
            make_message("Hi, I'm Ren. Any questions?", {"phase": "clarify", "hint_level_given": 0, "interview_over": False, "note": ""}, msg_id="m1"),
            make_message("Yes, it can't be empty.", {"phase": "clarify", "hint_level_given": 0, "interview_over": False, "note": "asked about empty input"}, msg_id="m2"),
            make_message("", {"phase": "code", "hint_level_given": 1, "interview_over": False, "note": ""}, msg_id="m3"),
        ]
    )
    monkeypatch.setattr(anthropic_provider, "get_client", lambda: fake)

    sid = client.post("/api/sessions", json={"problem_id": "best-time-to-buy-sell-stock"}).json()["id"]

    events = read_sse(client.post(f"/api/sessions/{sid}/turn", json={"event": "start", "code": "int x;"}))
    spoken = "".join(e["text"] for e in events if e["type"] == "text")
    assert "Ren" in spoken
    assert events[-2] == {"type": "state", "phase": "clarify", "hint_level_max": 0, "status": "active"}
    assert events[-1] == {"type": "done"}

    # Starting twice is rejected.
    assert client.post(f"/api/sessions/{sid}/turn", json={"event": "start"}).status_code == 409

    events = read_sse(client.post(f"/api/sessions/{sid}/turn", json={"event": "message", "text": "Can the array be empty?", "code": "int x;"}))
    assert events[-1]["type"] == "done"

    # The second request replays history verbatim and answers the pending tool_use first.
    second = fake.calls[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"] == fake.calls[1]["messages"][1]["content"]
    assert second[2]["content"][0] == {"type": "tool_result", "tool_use_id": "toolu_m1", "content": "Recorded."}
    assert "(unchanged since last turn)" in second[2]["content"][1]["text"]
    assert "Can the array be empty?" in second[2]["content"][1]["text"]

    # A silent turn (tool call only) still updates state.
    events = read_sse(client.post(f"/api/sessions/{sid}/turn", json={"event": "silence", "code": "int y;"}))
    assert not [e for e in events if e["type"] == "text"]
    assert events[-2]["phase"] == "code"
    assert events[-2]["hint_level_max"] == 1

    # History is append-only: each request's messages start with the previous request's messages.
    for prev, cur in zip(fake.calls, fake.calls[1:]):
        assert cur["messages"][: len(prev["messages"])] == prev["messages"]
    # System prompt and tools never change within a session.
    assert len({c["system"] for c in fake.calls}) == 1

    s = client.get(f"/api/sessions/{sid}").json()
    assert [t["kind"] for t in s["transcript"]] == ["ai", "candidate", "ai"]


def test_interview_over_ends_session(client, monkeypatch):
    fake = FakeClient(
        [make_message("Thanks, that's time.", {"phase": "wrap_up", "hint_level_given": 0, "interview_over": True, "note": ""})]
    )
    monkeypatch.setattr(anthropic_provider, "get_client", lambda: fake)
    sid = client.post("/api/sessions", json={"problem_id": "best-time-to-buy-sell-stock"}).json()["id"]
    events = read_sse(client.post(f"/api/sessions/{sid}/turn", json={"event": "time_up"}))
    assert events[-2]["status"] == "ended"
    assert client.post(f"/api/sessions/{sid}/turn", json={"event": "message", "text": "hi"}).status_code == 409


def test_refusal_is_not_saved(client, monkeypatch):
    fake = FakeClient(
        [
            make_message("", None, stop_reason="refusal", msg_id="r1"),
            make_message("Hello.", {"phase": "clarify", "hint_level_given": 0, "interview_over": False, "note": ""}, msg_id="m2"),
        ]
    )
    monkeypatch.setattr(anthropic_provider, "get_client", lambda: fake)
    sid = client.post("/api/sessions", json={"problem_id": "best-time-to-buy-sell-stock"}).json()["id"]
    events = read_sse(client.post(f"/api/sessions/{sid}/turn", json={"event": "start"}))
    assert "say that again" in events[0]["text"]
    read_sse(client.post(f"/api/sessions/{sid}/turn", json={"event": "message", "text": "hello?"}))
    # Two user messages in a row is valid; no assistant message from the refusal.
    assert [m["role"] for m in fake.calls[1]["messages"]] == ["user", "user"]


def test_failed_turn_is_rolled_back(client, monkeypatch):
    class BrokenClient(FakeClient):
        def _stream(self, **kwargs):
            raise TypeError("Could not resolve authentication method.")

    monkeypatch.setattr(anthropic_provider, "get_client", lambda: BrokenClient([]))
    sid = client.post("/api/sessions", json={"problem_id": "best-time-to-buy-sell-stock"}).json()["id"]
    events = read_sse(client.post(f"/api/sessions/{sid}/turn", json={"event": "start"}))
    assert events == [{"type": "error", "message": events[0]["message"]}]
    assert "API key" in events[0]["message"]

    # Nothing was kept, so the interview can still be started.
    fake = FakeClient([make_message("Hi.", {"phase": "clarify", "hint_level_given": 0, "interview_over": False, "note": ""})])
    monkeypatch.setattr(anthropic_provider, "get_client", lambda: fake)
    events = read_sse(client.post(f"/api/sessions/{sid}/turn", json={"event": "start"}))
    assert events[-1] == {"type": "done"}
    assert len(fake.calls[0]["messages"]) == 1


def test_client_log_is_written(client, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="client"):
        r = client.post(
            "/api/client-log",
            json={"session_id": "abcdef123456", "entries": [{"t": "10:00:00.000", "msg": "mic: heard", "data": {"text": "hi"}}]},
        )
    assert r.status_code == 204
    assert "[abcdef12] 10:00:00.000 mic: heard" in caplog.text


def test_turn_text_flags_an_interrupted_reply():
    from app.interviewer import build_turn_text
    from app.models import InterviewSession

    s = InterviewSession(problem_id="best-time-to-buy-sell-stock")
    assert "did not hear your previous reply" in build_turn_text(s, "message", "wait", None, interrupted=True)
    assert "did not hear" not in build_turn_text(s, "message", "hi", None)
