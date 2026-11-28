"""Server voice endpoint, with a fake Kokoro engine (no model files needed)."""

import io
import wave

import numpy as np

from app import tts
from tests.test_api import client  # noqa: F401  (fixture)


class FakeKokoro:
    def __init__(self):
        self.calls = []

    def create(self, text, voice, speed, lang):
        self.calls.append((text, voice, lang))
        return np.zeros(2400, dtype=np.float32), 24000


def test_tts_says_not_ready_until_the_voice_loads(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tts, "_engine", None)
    r = client.post("/api/tts", json={"text": "Hello there."})
    assert r.status_code == 503
    assert client.get("/api/health").json()["voice"]["voice"] == "af_heart"


def test_tts_returns_wav_audio(client, monkeypatch):  # noqa: F811
    fake = FakeKokoro()
    monkeypatch.setattr(tts, "_engine", fake)
    r = client.post("/api/tts", json={"text": "  Can you get it below O of n squared?  "})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (24000, 1, 2400)
    assert fake.calls == [("Can you get it below O of n squared?", "af_heart", "en-us")]


def test_tts_rejects_empty_text(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tts, "_engine", FakeKokoro())
    assert client.post("/api/tts", json={"text": "   "}).status_code == 400
