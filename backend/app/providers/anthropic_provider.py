import json
import logging
import time
from functools import lru_cache
from typing import Any

import anthropic

from ..config import settings
from .base import ProviderError, ToolCall, TurnOutcome

# Server-side refusal fallback: if a safety classifier declines a request,
# the API re-runs it on a fallback model inside the same call, so an
# interview is not broken by a rare false positive.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

log = logging.getLogger(__name__)


def _log_usage(kind: str, requested: str, final: Any, started: float) -> None:
    u = final.usage
    log.info(
        "anthropic %s %s%s: %.1fs stop=%s tokens(in=%s cache_read=%s cache_write=%s out=%s)",
        kind, final.model, "" if final.model == requested else f" (fallback from {requested})",
        time.monotonic() - started, final.stop_reason, u.input_tokens,
        getattr(u, "cache_read_input_tokens", None), getattr(u, "cache_creation_input_tokens", None), u.output_tokens,
    )


@lru_cache
def get_client() -> anthropic.AsyncAnthropic:
    if settings.anthropic_api_key:
        return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return anthropic.AsyncAnthropic()


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, interviewer_model: str, report_model: str):
        self.interviewer_model = interviewer_model
        self.report_model = report_model

    def user_entry(self, pending_tool_ids: list[str], text: str) -> list[dict]:
        content: list[dict] = [
            {"type": "tool_result", "tool_use_id": tid, "content": "Recorded."} for tid in pending_tool_ids
        ]
        content.append({"type": "text", "text": text})
        return content

    async def stream_turn(self, system: str, history: list[tuple[str, Any]], tool: dict):
        messages = [{"role": role, "content": stored} for role, stored in history]
        started = time.monotonic()
        async with get_client().beta.messages.stream(
            model=self.interviewer_model,
            max_tokens=8000,
            system=system,
            tools=[{**tool, "strict": True, "eager_input_streaming": True}],
            tool_choice={"type": "auto"},
            messages=messages,
            output_config={"effort": "low"},
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            async for ev in stream:
                if ev.type == "text" and ev.text:
                    yield ev.text
            final = await stream.get_final_message()
        _log_usage("turn", self.interviewer_model, final, started)
        log.debug("anthropic raw reply: %s", json.dumps([b.to_dict(exclude_none=True) for b in final.content])[:4000])

        if final.stop_reason == "refusal":
            yield TurnOutcome(status="refusal")
            return
        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=b.input if isinstance(b.input, dict) else None)
            for b in final.content
            if b.type == "tool_use"
        ]
        yield TurnOutcome(
            status="truncated" if final.stop_reason == "max_tokens" else "ok",
            spoken="".join(b.text for b in final.content if b.type == "text").strip(),
            tool_calls=tool_calls,
            # Stored verbatim (thinking and tool_use blocks included) and replayed unchanged.
            stored=[b.to_dict(exclude_none=True) for b in final.content],
        )

    async def generate_json(self, system: str, user_text: str, schema: dict, name: str) -> dict:
        started = time.monotonic()
        async with get_client().beta.messages.stream(
            model=self.report_model,
            max_tokens=32000,
            system=system,
            messages=[{"role": "user", "content": user_text}],
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            final = await stream.get_final_message()
        _log_usage("report", self.report_model, final, started)
        if final.stop_reason == "refusal":
            raise ProviderError("The report could not be generated for this interview.")
        if final.stop_reason == "max_tokens":
            raise ProviderError("The report was cut off. Please try again.")
        return json.loads(next((b.text for b in final.content if b.type == "text"), ""))

    def describe_error(self, exc: Exception) -> str:
        if isinstance(exc, ProviderError):
            return str(exc)
        if isinstance(exc, anthropic.AuthenticationError) or "authentication method" in str(exc):
            return "The Anthropic API key is missing or invalid. Set ANTHROPIC_API_KEY in backend/.env and restart the backend."
        if isinstance(exc, anthropic.RateLimitError):
            return "The AI service is rate limited right now. Wait a moment and try again."
        if isinstance(exc, anthropic.APIConnectionError):
            return "Could not reach the AI service. Check your internet connection and try again."
        return "The interviewer hit an error. Please try again."
