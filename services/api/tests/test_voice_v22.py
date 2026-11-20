"""STARK voice 2.2: push-to-talk ordering, hot-plugged microphones, diagnostics and the offline fallbacks."""

from __future__ import annotations

import json

import pytest

from stark.voice import GeminiVoice, VoiceError
from stark.voice.audio import Audio
from tests import test_voice
from tests.test_voice import FakeSD

voice_client = test_voice.voice_client  # the same fixture: a desktop engine with a mocked Gemini


def test_push_to_talk_release_before_the_microphone_opens_never_leaves_it_on():
    audio = Audio(FakeSD)
    gen = audio.begin()
    audio.release()  # the button came up before the worker thread reached the microphone
    assert audio.start(gen) is False and not audio.recording
    gen = audio.begin()
    assert audio.start(gen) is True and audio.recording
    audio.release()
    assert audio.stop()[:4] == b"RIFF" and not audio.recording


def test_a_microphone_plugged_in_after_launch_is_found():
    calls: list[str] = []

    class HotPlugSD(FakeSD):
        plugged = False

        @staticmethod
        def query_devices(kind=None):
            if kind is None:
                return [{"name": "USB Mic", "max_input_channels": 1, "max_output_channels": 0},
                        {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2}]
            if kind == "input" and not HotPlugSD.plugged:
                raise ValueError("no default input")
            return {"name": "USB Mic" if kind == "input" else "Speakers"}

        @staticmethod
        def _terminate():
            calls.append("t")

        @staticmethod
        def _initialize():
            calls.append("i")
            HotPlugSD.plugged = True

    audio = Audio(HotPlugSD)
    assert audio.available() == (False, "No microphone or speaker found.")
    assert audio.available(rescan=True) == (True, "") and calls == ["t", "i"]
    d = audio.devices()
    assert d["ok"] and d["inputs"] == ["USB Mic"] and d["default_output"] == "Speakers"


def test_voice_diagnostics_and_offline_fallbacks(voice_client, settings, monkeypatch):
    from stark.voice import offline

    c, _ = voice_client
    d = c.get("/api/voice/diagnostics").json()
    assert d["desktop"] and d["key"] and "inputs" in d and not d["recording"]

    async def no_net(*a, **k):
        raise VoiceError("network")
        yield  # pragma: no cover

    monkeypatch.setattr(GeminiVoice, "transcribe", lambda *a, **k: no_net().__anext__())
    monkeypatch.setattr(GeminiVoice, "speak_stream", lambda *a, **k: no_net())
    monkeypatch.setattr(offline, "ready", lambda: True)
    monkeypatch.setattr(offline, "transcribe", lambda wav, mime: "offline words")
    c.post("/api/voice/listen/start")
    assert c.post("/api/voice/listen/stop").json() == {"text": "offline words", "offline": True}
    r = c.post("/api/voice/speak", json={"agent_id": "vision", "text": "Hello"}).json()
    assert r["fallback"] == "system" and r["played"] is False and "gem-key" not in json.dumps(r)
    monkeypatch.setattr(offline, "ready", lambda: False)
    c.post("/api/voice/listen/start")
    assert c.post("/api/voice/listen/stop").status_code == 502


def test_offline_wav_decoding():
    pytest.importorskip("numpy")  # installed with the optional on-device speech engine
    import io
    import wave

    from stark.voice import offline

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(b"\x00\x40" * 1600)
    s = offline.wav_samples(buf.getvalue())
    assert len(s) == 1600 and abs(float(s[0]) - 0.5) < 1e-3
    with pytest.raises(offline.OfflineError):
        bad = io.BytesIO()
        with wave.open(bad, "wb") as w:
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(44100), w.writeframes(b"\x00" * 8)
        offline.wav_samples(bad.getvalue())
