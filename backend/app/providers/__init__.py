from ..config import settings
from .anthropic_provider import AnthropicProvider
from .base import Provider, ProviderError, ToolCall, TurnOutcome
from .groq_provider import GroqProvider

DEFAULT_MODELS = {
    "anthropic": {"interviewer": "claude-sonnet-5-5", "report": "claude-opus-5-5"},
    "groq": {"interviewer": "openai/gpt-oss-120b", "report": "openai/gpt-oss-120b"},
}

PROVIDERS = {"anthropic": AnthropicProvider, "groq": GroqProvider}


def get_provider(name: str | None = None) -> Provider:
    """The provider for a session (or the configured default for new sessions)."""
    name = name or settings.llm_provider
    if name not in PROVIDERS:
        raise ValueError(f"Unknown LLM provider '{name}'. Use one of: {', '.join(PROVIDERS)}")
    same = name == settings.llm_provider
    return PROVIDERS[name](
        interviewer_model=(same and settings.interviewer_model) or DEFAULT_MODELS[name]["interviewer"],
        report_model=(same and settings.report_model) or DEFAULT_MODELS[name]["report"],
    )


__all__ = ["Provider", "ProviderError", "ToolCall", "TurnOutcome", "get_provider", "PROVIDERS"]
