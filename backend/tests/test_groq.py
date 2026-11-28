"""Groq provider tests with a fake Groq client (no network, no API key needed)."""

import json
from types import SimpleNamespace

import pytest

from app.config import settings
from app.providers import groq_provider
from tests.test_api import client, read_sse  # noqa: F401  (fixture)


def chunk(content=None, tool_calls=None, finish=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


def tc(index, id=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments))


class FakeGroq:
    def __init__(self, streams: list[list], json_reply: dict | None = None):
        self.streams = streams
        self.json_reply = json_reply
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs)))
        if kwargs.get("stream"):
            chunks = self.streams.pop(0)

            async def gen():
                for c in chunks:
                    yield c

            return gen()
        msg = SimpleNamespace(content=json.dumps(self.json_reply))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


STATE = {"phase": "clarify", "hint_level_given": 0, "interview_over": False, "note": "asked about edge cases"}


@pytest.fixture
def groq_client(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "llm_provider", "groq")
    return client


def test_groq_turns_and_report(groq_client, monkeypatch):
    args = json.dumps(STATE)
    fake = FakeGroq(
        [
            # Text, then a tool call split across fragments.
            [
                chunk("Hi, I'm Ren. "),
                chunk("Any questions?"),
                chunk(tool_calls=[tc(0, id="call_a", name="update_interview", arguments=args[:20])]),
                chunk(tool_calls=[tc(0, arguments=args[20:])], finish="tool_calls"),
            ],
            # Words only inside the tool call's "say" field (the usual Groq shape).
            [chunk(tool_calls=[tc(0, id="call_b", name="update_interview", arguments=json.dumps({"say": "Good question. No, it is never empty.", **STATE}))], finish="tool_calls")],
        ],
        json_reply={
            "verdict": "hire",
            "summary": "Solid.",
            "scores": [{"area": "approach", "score": 9, "evidence": "e", "how_to_improve": "h"}],
            "code_review": {"is_correct": True, "time_complexity": "O(n)", "space_complexity": "O(1)", "traced_cases": [], "issues": []},
            "strengths": [],
            "top_improvements": [],
            "strong_candidate_would": "x",
        },
    )
    monkeypatch.setattr(groq_provider, "get_client", lambda: fake)

    c = groq_client
    sid = c.post("/api/sessions", json={"problem_id": "best-time-to-buy-sell-stock"}).json()["id"]

    events = read_sse(c.post(f"/api/sessions/{sid}/turn", json={"event": "start", "code": "x"}))
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Hi, I'm Ren. Any questions?"
    assert events[-2]["phase"] == "clarify"

    events = read_sse(c.post(f"/api/sessions/{sid}/turn", json={"event": "message", "text": "Can it be empty?", "code": "x"}))
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Good question. No, it is never empty."
    transcript = c.get(f"/api/sessions/{sid}").json()["transcript"]
    assert transcript[-1] == {**transcript[-1], "kind": "ai", "text": "Good question. No, it is never empty."}

    first, second = fake.calls[0], fake.calls[1]
    assert first["model"] == "openai/gpt-oss-120b"
    assert first["tools"][0]["function"]["name"] == "update_interview"
    assert first["tool_choice"] == "auto"
    assert first["tools"][0]["function"]["parameters"]["required"][0] == "say"
    roles = [m["role"] for m in second["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    assistant = second["messages"][2]
    assert assistant["tool_calls"][0]["function"]["arguments"] == args  # fragments merged
    assert second["messages"][3]["tool_call_id"] == "call_a"
    # Append-only history.
    assert second["messages"][: len(first["messages"])] == first["messages"]

    report = c.post(f"/api/sessions/{sid}/report").json()
    assert report["verdict"] == "hire"
    assert report["scores"][0]["score"] == 4  # clamped into 1-4
    schema = fake.calls[2]["response_format"]["json_schema"]
    assert schema["strict"] is True
    score_schema = schema["schema"]["properties"]["scores"]["items"]["properties"]["score"]
    assert "enum" not in score_schema
    assert "asked about edge cases" in fake.calls[2]["messages"][1]["content"]


def test_groq_missing_key_message(groq_client, monkeypatch):
    class Broken(FakeGroq):
        async def _create(self, **kwargs):
            import groq

            raise groq.GroqError("The api_key client option must be set")

    monkeypatch.setattr(groq_provider, "get_client", lambda: Broken([]))
    sid = groq_client.post("/api/sessions", json={"problem_id": "best-time-to-buy-sell-stock"}).json()["id"]
    events = read_sse(groq_client.post(f"/api/sessions/{sid}/turn", json={"event": "start"}))
    assert events[0]["type"] == "error"
    assert "GROQ_API_KEY" in events[0]["message"]


def test_groq_empty_reply_is_retried_and_never_saved(groq_client, monkeypatch):
    args = json.dumps({"say": "Take your time.", **STATE})
    empty = [chunk(finish="stop")]
    fake = FakeGroq(
        [
            empty,  # turn 1: empty, then a real reply on the retry
            [chunk(tool_calls=[tc(0, id="call_a", name="update_interview", arguments=args)], finish="tool_calls")],
            empty, empty, empty,  # turn 2: empty on every attempt
        ]
    )
    monkeypatch.setattr(groq_provider, "get_client", lambda: fake)
    c = groq_client
    sid = c.post("/api/sessions", json={"problem_id": "best-time-to-buy-sell-stock"}).json()["id"]

    events = read_sse(c.post(f"/api/sessions/{sid}/turn", json={"event": "start", "code": "x"}))
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Take your time."
    assert fake.calls[1]["messages"][-1]["role"] == "system"  # the retry nudges the model

    events = read_sse(c.post(f"/api/sessions/{sid}/turn", json={"event": "message", "text": "Hmm", "code": "x"}))
    assert events[0]["type"] == "error" and "Send again" in events[0]["message"]
    transcript = c.get(f"/api/sessions/{sid}").json()["transcript"]
    assert [t["kind"] for t in transcript] == ["ai"]  # the failed turn was rolled back, nothing blank saved


def test_groq_skips_blank_replies_already_in_history():
    from app.providers.groq_provider import _is_blank_assistant

    assert _is_blank_assistant({"role": "assistant", "content": ""})
    assert not _is_blank_assistant({"role": "assistant", "content": None, "tool_calls": [{}]})
    assert not _is_blank_assistant({"role": "user", "content": ""})
