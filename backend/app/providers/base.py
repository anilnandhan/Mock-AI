"""The interface every AI provider implements.

Each interview stays on one provider from start to finish, because the stored
conversation is kept in that provider's own message format. That lets each
provider replay its history exactly as it was sent (append-only).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict | None  # None when the arguments could not be parsed


@dataclass
class TurnOutcome:
    status: Literal["ok", "refusal", "truncated"]
    spoken: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # What to store as the assistant entry, in the provider's own format.
    stored: Any = None


class ProviderError(Exception):
    """A failure with a message that is safe to show the candidate."""


class Provider(Protocol):
    name: str

    def user_entry(self, pending_tool_ids: list[str], text: str) -> Any:
        """The stored form of one user turn, answering any pending tool calls first."""

    def stream_turn(
        self, system: str, history: list[tuple[str, Any]], tool: dict
    ) -> AsyncIterator["str | TurnOutcome"]:
        """Yields text chunks as they arrive, then exactly one TurnOutcome."""

    async def generate_json(self, system: str, user_text: str, schema: dict, name: str) -> dict:
        """One structured-output call. Raises ProviderError on refusal or truncation."""

    def describe_error(self, exc: Exception) -> str:
        """A short, user-facing explanation of an exception from this provider."""
