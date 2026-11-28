"""Alex's voice, made on this machine with Kokoro (open source, free, works offline).

The model files (about 350 MB) are downloaded into backend/models/ the first time
the server starts, then loaded in the background. Until the voice is ready, or if
it fails, /api/tts answers 503 and the browser falls back to its own voice.
"""

import io
import logging
import shutil
import threading
import time
import urllib.request
import wave

import numpy as np

from .config import settings

log = logging.getLogger("app.tts")

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"

# off | loading | ready | error
_state = "off"
_engine = None
_lock = threading.Lock()  # one sentence at a time keeps sentences in order and the CPU free


class VoiceNotReady(Exception):
    pass


def status() -> dict:
    return {"state": _state, "voice": settings.tts_voice}


def _lang() -> str:
    # Kokoro voice names start with an accent code: "b" is British English.
    return settings.tts_lang or ("en-gb" if settings.tts_voice.startswith("b") else "en-us")


def start() -> None:
    """Begin downloading/loading the voice in the background. Returns at once."""
    global _state
    if not settings.tts_enabled:
        log.info("voice: server voice is off (TTS_ENABLED=false); the browser voice is used")
        return
    if _state in ("loading", "ready"):
        return
    _state = "loading"
    threading.Thread(target=_load, name="tts-load", daemon=True).start()


def _download(name: str) -> None:
    dest = settings.tts_model_dir / name
    tmp = dest.with_name(name + ".part")
    log.info("voice: downloading %s (first run only, this can take a minute)", name)
    started = time.monotonic()
    with urllib.request.urlopen(MODEL_URL + name, timeout=60) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out, 1 << 20)
    tmp.replace(dest)
    log.info("voice: downloaded %s in %.0fs", name, time.monotonic() - started)


def _load() -> None:
    global _state, _engine
    try:
        settings.tts_model_dir.mkdir(parents=True, exist_ok=True)
        for name in (MODEL_FILE, VOICES_FILE):
            if not (settings.tts_model_dir / name).exists():
                _download(name)

        from kokoro_onnx import Kokoro

        started = time.monotonic()
        engine = Kokoro(str(settings.tts_model_dir / MODEL_FILE), str(settings.tts_model_dir / VOICES_FILE))
        if settings.tts_voice not in engine.get_voices():
            raise ValueError(f"unknown TTS_VOICE {settings.tts_voice!r}; available: {', '.join(engine.get_voices())}")
        engine.create("Ready.", voice=settings.tts_voice, speed=settings.tts_speed, lang=_lang())  # warm up
        _engine = engine
        _state = "ready"
        log.info("voice: ready (%s) in %.1fs", settings.tts_voice, time.monotonic() - started)
    except Exception:
        _state = "error"
        log.exception("voice: could not load the server voice; the browser voice is used instead")


def synthesize(text: str) -> bytes:
    """One sentence of speech as a 16-bit mono WAV file."""
    if _engine is None:
        raise VoiceNotReady(_state)
    started = time.monotonic()
    with _lock:
        audio, rate = _engine.create(text, voice=settings.tts_voice, speed=settings.tts_speed, lang=_lang())
    log.debug("voice: %.2fs to make %.1fs of speech: %s", time.monotonic() - started, len(audio) / rate, text[:60])
    return to_wav(audio, rate)


def to_wav(audio: np.ndarray, rate: int) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
