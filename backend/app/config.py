from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # Which AI provider new interviews use: "anthropic" or "groq".
    llm_provider: Literal["anthropic", "groq"] = "anthropic"

    # Read by each SDK if set; otherwise the SDK looks in the environment.
    anthropic_api_key: str | None = None
    groq_api_key: str | None = None

    # Leave empty to use the provider's default models (see app/providers/__init__.py).
    interviewer_model: str | None = None
    report_model: str | None = None

    database_url: str = f"sqlite:///{BASE_DIR / 'mock_ai.db'}"
    problems_dir: Path = BASE_DIR / "problems"

    interview_minutes: int = 45
    # INFO logs every turn; DEBUG also logs full prompts and raw replies.
    log_level: str = "INFO"

    # Ren's voice (Kokoro, runs locally). Set TTS_ENABLED=false to use the browser voice.
    tts_enabled: bool = True
    tts_voice: str = "af_heart"  # others: af_sarah, af_bella, bf_emma, hf_alpha (Indian English) ...
    tts_speed: float = 1.0
    tts_lang: str | None = None  # default: from the voice name (en-us, or en-gb for b* voices)
    tts_model_dir: Path = BASE_DIR / "models"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


settings = Settings()
