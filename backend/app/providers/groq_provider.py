"""Groq (OpenAI-style chat completions) for cheap, fast early testing."""

import copy
import json
import logging
import time
from functools import lru_cache
from typing import Any

import groq

from ..config import settings
from .base import ProviderError, ToolCall, TurnOutcome

log = logging.getLogger(__name__)


@lru_cache
def get_client() -> groq.AsyncGroq:
    if settings.groq_api_key:
        return groq.AsyncGroq(api_key=settings.groq_api_key)
    return groq.AsyncGroq()  # reads GROQ_API_KEY from the environment


def _loosen_for_groq(schema: Any) -> Any:
    """Groq's strict mode documents only string enums, so turn integer enums
    into plain integers. Values are range-checked after parsing."""
    if isinstance(schema, dict):
        out = {k: _loosen_for_groq(v) for k, v in schema.items()}
        if out.get("type") == "integer" and "enum" in out:
            out.pop("enum")
        return out
    if isinstance(schema, list):
        return [_loosen_for_groq(v) for v in schema]
    return schema


MAX_ATTEMPTS = 3
EMPTY_REPLY_NUDGE = (
    "Your last reply was empty. Respond to the candidate now: call update_interview with what you "
    "say in `say`. If they are mid-thought, a short acknowledgement or question is fine."
)


def _is_blank_assistant(m: dict) -> bool:
    return m.get("role") == "assistant" and not m.get("tool_calls") and not (m.get("content") or "").strip()


class GroqProvider:
    name = "groq"

    def __init__(self, interviewer_model: str, report_model: str):
        self.interviewer_model = interviewer_model
        self.report_model = report_model

    def user_entry(self, pending_tool_ids: list[str], text: str) -> list[dict]:
        entries: list[dict] = [
            {"role": "tool", "tool_call_id": tid, "name": "update_interview", "content": "Recorded."}
            for tid in pending_tool_ids
        ]
        entries.append({"role": "user", "content": text})
        return entries

    async def stream_turn(self, system: str, history: list[tuple[str, Any]], tool: dict):
        messages: list[dict] = [{"role": "system", "content": system}]
        for _role, stored in history:
            # Each stored entry is a list of chat messages. Skip blank assistant
            # replies saved by older versions; they make the model go quiet again.
            messages.extend(m for m in stored if not _is_blank_assistant(m))

        # Open models on Groq usually either speak or call a tool, not both. So the
        # spoken reply goes inside the tool call as a "say" field.
        schema = copy.deepcopy(tool["input_schema"])
        schema["properties"] = {
            "say": {
                "type": "string",
                "description": "Exactly what you say out loud to the candidate this turn. Empty string to stay silent.",
            },
            **schema["properties"],
        }
        schema["required"] = ["say", *schema["required"]]

        tools = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": (
                        "Call this exactly once every turn. Put everything you say to the candidate in "
                        "`say`, then record the interview state."
                    ),
                    "parameters": schema,
                },
            }
        ]

        # gpt-oss on Groq occasionally returns a completely empty reply (no text,
        # no tool call). Nothing has been shown yet in that case, so just ask again.
        for attempt in range(MAX_ATTEMPTS):
            extra = [] if attempt == 0 else [{"role": "system", "content": EMPTY_REPLY_NUDGE}]
            outcome = None
            async for item in self._attempt(messages + extra, tools):
                if isinstance(item, TurnOutcome):
                    outcome = item
                else:
                    yield item
            assert outcome is not None
            if outcome.spoken or outcome.tool_calls or outcome.status != "ok":
                yield outcome
                return
            log.warning("Groq returned an empty reply (attempt %d of %d)", attempt + 1, MAX_ATTEMPTS)
        raise ProviderError("Ren didn't answer that time. Press Send again.")

    async def _attempt(self, messages: list[dict], tools: list[dict]):
        stream = await get_client().chat.completions.create(
            model=self.interviewer_model,
            messages=messages,
            tools=tools,
            # "required" makes Groq error out when the model answers in plain text,
            # so accept either form: words in `say`, words as text, or both.
            tool_choice="auto",
            parallel_tool_calls=False,
            reasoning_effort="low",
            max_completion_tokens=4096,
            stream=True,
        )

        text_parts: list[str] = []
        calls: dict[int, dict] = {}
        finish = None
        usage = None
        t0 = time.monotonic()
        async for chunk in stream:
            # Groq reports token usage on the last chunk under x_groq.
            x_groq = getattr(chunk, "x_groq", None)
            if x_groq is not None and getattr(x_groq, "usage", None) is not None:
                usage = x_groq.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta.content:
                text_parts.append(delta.content)
                yield delta.content
            # Tool calls may arrive whole or in fragments; merge them by index.
            for tc in delta.tool_calls or []:
                acc = calls.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
                if tc.id:
                    acc["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        acc["name"] = tc.function.name
                    if tc.function.arguments:
                        acc["arguments"] += tc.function.arguments
            if choice.finish_reason:
                finish = choice.finish_reason

        content_text = "".join(text_parts).strip()
        spoken_from_tool = ""
        tool_calls: list[ToolCall] = []
        stored_calls: list[dict] = []
        for i, acc in sorted(calls.items()):
            call_id = acc["id"] or f"call_{i}"
            try:
                args = json.loads(acc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = None
            if isinstance(args, dict) and isinstance(args.get("say"), str):
                spoken_from_tool = args["say"].strip()
            tool_calls.append(ToolCall(id=call_id, name=acc["name"], input=args if isinstance(args, dict) else None))
            stored_calls.append(
                {"id": call_id, "type": "function", "function": {"name": acc["name"], "arguments": acc["arguments"]}}
            )

        # The words arrive inside the tool call, so send them as one piece now.
        if spoken_from_tool and spoken_from_tool != content_text:
            yield ("\n" if content_text else "") + spoken_from_tool
        spoken = content_text if spoken_from_tool in ("", content_text) else f"{content_text} {spoken_from_tool}".strip()

        log.info(
            "groq %s: %.1fs finish=%s text_chars=%d tool_calls=%s say_chars=%d tokens(in=%s out=%s)",
            self.interviewer_model, time.monotonic() - t0, finish, len(content_text),
            [c.name for c in tool_calls], len(spoken_from_tool),
            getattr(usage, "prompt_tokens", "?"), getattr(usage, "completion_tokens", "?"),
        )
        for call, stored in zip(tool_calls, stored_calls):
            if call.input is None:
                log.warning("groq tool call %s had unparseable arguments: %r", call.name, stored["function"]["arguments"][:500])
        log.debug("groq raw reply: content=%r tool_args=%s", content_text, [a["arguments"] for a in calls.values()])

        assistant: dict = {"role": "assistant", "content": content_text or None}
        if stored_calls:
            assistant["tool_calls"] = stored_calls
        if assistant["content"] is None and not stored_calls:
            assistant["content"] = ""

        yield TurnOutcome(
            status="truncated" if finish == "length" else "ok",
            spoken=spoken,
            tool_calls=tool_calls,
            stored=[assistant],
        )

    async def generate_json(self, system: str, user_text: str, schema: dict, name: str) -> dict:
        # Structured outputs on Groq do not support streaming. gpt-oss sometimes
        # writes JSON that Groq rejects as off-schema; that is worth a retry.
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = await get_client().chat.completions.create(
                    model=self.report_model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user_text}],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": name, "strict": True, "schema": _loosen_for_groq(copy.deepcopy(schema))},
                    },
                    reasoning_effort="medium",
                    max_completion_tokens=8192,
                )
                break
            except groq.BadRequestError as e:
                if "json_validate_failed" not in str(e) or attempt == MAX_ATTEMPTS - 1:
                    raise
                log.warning("Groq report JSON failed validation (attempt %d of %d)", attempt + 1, MAX_ATTEMPTS)
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise ProviderError("The report was cut off. Please try again.")
        data = json.loads(choice.message.content or "")
        # Restore the 1-4 range that the loosened schema could not enforce.
        for score in data.get("scores", []):
            if isinstance(score.get("score"), int):
                score["score"] = min(4, max(1, score["score"]))
        return data

    def describe_error(self, exc: Exception) -> str:
        if isinstance(exc, ProviderError):
            return str(exc)
        if isinstance(exc, groq.AuthenticationError) or (
            isinstance(exc, groq.GroqError) and "api_key" in str(exc).lower()
        ):
            return "The Groq API key is missing or invalid. Set GROQ_API_KEY in backend/.env and restart the backend."
        if isinstance(exc, groq.RateLimitError) or getattr(exc, "status_code", None) == 413:
            return (
                "Groq's rate limit was hit (the free tier allows only a few thousand tokens per minute). "
                "Wait a minute and try again, or use a shorter interview for testing."
            )
        if isinstance(exc, groq.APIConnectionError):
            return "Could not reach Groq. Check your internet connection and try again."
        return "The interviewer hit an error. Please try again."
