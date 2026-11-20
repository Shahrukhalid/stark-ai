"""Microphone and speakers for the desktop app (PortAudio through sounddevice, bundled with the installers).

Only used when the engine runs on the user's own computer (the desktop app). Nothing is recorded unless the
user holds the talk button; recording stops by itself after MAX_SECONDS.
"""

from __future__ import annotations

import array
import math
import queue
import threading
import time
from typing import Any

from .gemini import wav_bytes

RATE = 16000
MAX_SECONDS = 60


def rms(pcm: bytes) -> float:
    """Loudness of 16-bit audio, 0..1 (speech peaks around 0.3)."""
    a = array.array("h", pcm[: len(pcm) // 2 * 2])
    if not a:
        return 0.0
    step = max(1, len(a) // 400)
    s = a[::step]
    return min(1.0, math.sqrt(sum(x * x for x in s) / len(s)) / 8000)


class AudioUnavailable(RuntimeError):
    pass


RESCAN_EVERY_S = 5.0


class Audio:
    def __init__(self, sd: Any = None):
        self._sd = sd
        self._lock = threading.Lock()
        self._stream: Any = None
        self._buf = bytearray()
        self._out: Any = None
        self._stop_play = threading.Event()
        self.mic_level = 0.0
        self.out_level = 0.0
        self.playing = False
        # Push-to-talk ordering: every press/release bumps the generation synchronously (in request order), so a
        # start that reaches the microphone after its release was already handled never opens the stream.
        self._gen = 0
        self._rescanned = float("-inf")

    def _module(self):
        if self._sd is None:
            try:
                import sounddevice  # noqa: PLC0415 - optional, desktop only
            except (ImportError, OSError) as exc:  # no PortAudio on this machine
                raise AudioUnavailable("No audio support on this computer.") from exc
            self._sd = sounddevice
        return self._sd

    def _rescan(self, sd: Any) -> None:
        """PortAudio caches the device list at start-up: a microphone plugged in later (or a headset that
        Bluetooth connects after launch) is invisible until it is re-initialised. Done only while idle."""
        # Re-initialising PortAudio while another thread opens a stream can crash it, so this never waits for or races
        # with start()/stop() (non-blocking lock), and runs at most every few seconds however often the page asks.
        now = time.monotonic()
        if self._stream is not None or self.playing or now - self._rescanned < RESCAN_EVERY_S:
            return
        if not self._lock.acquire(blocking=False):
            return
        try:
            if self._stream is not None or self.playing:
                return
            term, init = getattr(sd, "_terminate", None), getattr(sd, "_initialize", None)
            if callable(term) and callable(init):
                try:
                    term()
                    init()
                except Exception:  # noqa: BLE001 - keep the old list rather than break audio
                    pass
            self._rescanned = now
        finally:
            self._lock.release()

    def available(self, rescan: bool = False) -> tuple[bool, str]:
        try:
            sd = self._module()
            if rescan:
                self._rescan(sd)
            sd.query_devices(kind="input")
            sd.query_devices(kind="output")
        except AudioUnavailable:
            return False, "No audio support on this computer."
        except Exception:  # noqa: BLE001 - PortAudio raises its own error types
            return False, "No microphone or speaker found."
        return True, ""

    def devices(self) -> dict[str, Any]:
        """Real-device diagnostics: what PortAudio sees right now (names only, nothing is recorded)."""
        try:
            sd = self._module()
        except AudioUnavailable as exc:
            return {"ok": False, "reason": str(exc), "inputs": [], "outputs": []}
        self._rescan(sd)
        try:
            all_ = list(sd.query_devices())
        except Exception:  # noqa: BLE001
            all_ = []

        def default(kind: str) -> str:
            try:
                return str(sd.query_devices(kind=kind).get("name", ""))
            except Exception:  # noqa: BLE001
                return ""
        ins = [str(d.get("name", "")) for d in all_ if isinstance(d, dict) and d.get("max_input_channels", 0) > 0]
        outs = [str(d.get("name", "")) for d in all_ if isinstance(d, dict) and d.get("max_output_channels", 0) > 0]
        di, do = default("input"), default("output")
        return {"ok": bool(di and do), "reason": "" if di and do else "No microphone or speaker found.",
                "inputs": ins, "outputs": outs, "default_input": di, "default_output": do}

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def begin(self) -> int:
        """Call on the request thread when talk is pressed; pass the result to start()."""
        self._gen += 1
        return self._gen

    def release(self) -> None:
        """Call on the request thread when talk is released (before stop() runs in a worker)."""
        self._gen += 1

    def start(self, gen: int | None = None) -> bool:
        """Open the microphone. False when the press was already released (nothing is opened)."""
        sd = self._module()
        with self._lock:
            if gen is not None and gen != self._gen:
                return False
            self._close_input()
            self._buf = bytearray()
            limit = RATE * 2 * MAX_SECONDS

            def take(indata, frames, time_info, status):  # noqa: ARG001 - PortAudio callback signature
                if len(self._buf) < limit:
                    self._buf.extend(bytes(indata))
                self.mic_level = rms(bytes(indata))

            try:
                self._stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", callback=take)
                self._stream.start()
            except Exception as exc:  # noqa: BLE001
                self._stream = None
                raise AudioUnavailable("The microphone could not be opened. Allow microphone access for "
                                       "STARK AI in your system settings.") from exc
        return True

    def stream(self, on_chunk) -> None:
        """Record and hand each block to on_chunk(bytes) as it arrives (fast mode). Stop with cancel()."""
        sd = self._module()
        with self._lock:
            self._close_input()

            def take(indata, frames, time_info, status):  # noqa: ARG001 - PortAudio callback signature
                data = bytes(indata)
                self.mic_level = rms(data)
                on_chunk(data)

            try:
                self._stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=1600,
                                                 callback=take)
                self._stream.start()
            except Exception as exc:  # noqa: BLE001
                self._stream = None
                raise AudioUnavailable("The microphone could not be opened.") from exc

    def stop(self) -> bytes:
        """Stop recording; return a WAV file (empty bytes when nothing was recorded)."""
        with self._lock:
            self._close_input()
            pcm, self._buf = bytes(self._buf), bytearray()
        return wav_bytes(pcm, RATE) if len(pcm) > RATE * 2 // 4 else b""  # under 0.25 s: nothing said

    def cancel(self) -> None:
        with self._lock:
            self._close_input()
            self._buf = bytearray()

    def _close_input(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
        self.mic_level = 0.0

    def player(self, rate: int) -> "Player":
        """Start a player that plays audio as it is fed; a new reply or stop_playing() cuts it off."""
        sd = self._module()
        self.stop_playing()
        self._stop_play = stop = threading.Event()
        return Player(sd, rate, stop, self)

    def play(self, pcm: bytes, rate: int) -> None:
        p = self.player(rate)
        p.feed(pcm)
        p.close()

    def stop_playing(self) -> None:
        self._stop_play.set()


class Player:
    def __init__(self, sd: Any, rate: int, stop: threading.Event, owner: "Audio | None" = None):
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self._stop = stop

        def run() -> None:
            if owner:
                owner.playing = True
            try:
                with sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16") as out:
                    step = rate // 10 * 2  # 100 ms
                    while not stop.is_set():
                        pcm = self._q.get()
                        if pcm is None:
                            break
                        for i in range(0, len(pcm), step):
                            if stop.is_set():
                                break
                            if owner:
                                owner.out_level = rms(pcm[i:i + step])
                            out.write(pcm[i:i + step])
            except Exception:  # noqa: BLE001 - a missing speaker must not crash the engine
                pass
            finally:
                if owner and owner._stop_play is stop:
                    owner.playing = False
                    owner.out_level = 0.0

        self.thread = threading.Thread(target=run, name="stark-voice-play", daemon=True)
        self.thread.start()

    def feed(self, pcm: bytes) -> None:
        if not self._stop.is_set():
            self._q.put(pcm)

    def close(self) -> None:
        self._q.put(None)
