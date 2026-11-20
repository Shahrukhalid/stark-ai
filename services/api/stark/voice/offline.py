"""On-device speech-to-text fallback (whisper.cpp through pywhispercpp, the same engine as the video studio).

Used only when Gemini can't answer (no key, offline, quota, timeout) and the owner has downloaded a whisper model
in the video studio. Nothing leaves the computer on this path.
"""

from __future__ import annotations

import array
import io
import subprocess
import wave
from pathlib import Path
from typing import Any

_settings: Any = None
ORDER = ("base", "small", "tiny", "medium", "large-v3-turbo", "large-v3")


class OfflineError(RuntimeError):
    pass


def configure(settings: Any) -> None:
    global _settings
    _settings = settings


def _model() -> Path | None:
    if _settings is None or not getattr(_settings, "data_dir", None):
        return None
    d = Path(_settings.data_dir) / "models" / "whisper"
    for name in ORDER:
        f = d / f"ggml-{name}.bin"
        if f.is_file():
            return f
    found = sorted(d.glob("ggml-*.bin")) if d.is_dir() else []
    return found[0] if found else None


def model_name() -> str:
    m = _model()
    return m.stem.removeprefix("ggml-") if m else ""


def ready() -> bool:
    if _model() is None:
        return False
    try:
        import pywhispercpp.model  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001
        return False
    return True


def wav_samples(data: bytes):
    """16 kHz mono 16-bit WAV (what the desktop microphone records) to float samples, without FFmpeg."""
    import numpy as np  # noqa: PLC0415

    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
            raise OfflineError("unsupported wav")
        pcm = array.array("h", w.readframes(w.getnframes()))
    return np.asarray(pcm, dtype=np.float32) / 32768.0


def transcribe(data: bytes, mime: str) -> str:
    model = _model()
    if model is None:
        raise OfflineError("no model")
    from .. import video  # noqa: PLC0415

    try:
        if mime == "audio/wav":
            samples = wav_samples(data)
        else:  # browser recordings (webm/mp4) need FFmpeg to decode
            exe = video.ffmpeg_path()
            if not exe:
                raise OfflineError("no ffmpeg")
            r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000",
                                "-f", "s16le", "pipe:1"], input=data, capture_output=True, timeout=60)
            if r.returncode != 0 or not r.stdout:
                raise OfflineError("decode failed")
            import numpy as np  # noqa: PLC0415

            samples = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        lang = getattr(_settings, "voice_language", "auto") or "auto"
        segs = video.transcribe_audio(model, samples, lang if lang in ("en", "ur") else "auto")
    except OfflineError:
        raise
    except Exception as exc:  # noqa: BLE001 - whisper/numpy failures become one readable error upstream
        raise OfflineError(str(exc)[:120]) from exc
    return " ".join(s["text"] for s in segs).strip()
