"""Logging to the console and to backend/logs/mock_ai.log (rotated).

Set LOG_LEVEL=DEBUG in .env to also log full prompts and raw model replies.
Browser events (mic, speech, turns) arrive through /api/client-log under the
"client" logger, so one file shows both sides of an interview.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import BASE_DIR, settings

LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "mock_ai.log"
FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"


def setup_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_mock_ai_configured", False):
        return
    LOG_DIR.mkdir(exist_ok=True)
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(FORMAT, "%H:%M:%S"))
    file = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file.setFormatter(logging.Formatter(FORMAT))

    root.setLevel(logging.INFO)
    # Tests use fake AI clients; keep their output out of the real log file.
    root.handlers = [console] if "pytest" in sys.modules else [console, file]
    # Our own code follows LOG_LEVEL; libraries stay at INFO so DEBUG isn't flooded.
    for name in ("app", "client"):
        logging.getLogger(name).setLevel(level)
    root._mock_ai_configured = True  # type: ignore[attr-defined]
