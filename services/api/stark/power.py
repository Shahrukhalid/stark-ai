"""Keep-awake helper for long video rendering and autopilot queue operations.

Keeps the computer from sleeping while exports or background jobs are active (display may sleep).
On Windows uses kernel32 SetThreadExecutionState; on macOS uses caffeinate.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from typing import Iterator

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def set_keep_awake(enabled: bool) -> bool:
    """Toggle sleep prevention. Windows only (safe no-op on other platforms)."""
    if sys.platform == "win32":
        try:
            import ctypes

            flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if enabled else 0)
            return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
        except Exception:
            return False
    return False


@contextlib.contextmanager
def keep_awake() -> Iterator[None]:
    """Context manager preventing system sleep during critical rendering and processing."""
    proc: subprocess.Popen | None = None
    if sys.platform == "win32":
        set_keep_awake(True)
    elif sys.platform == "darwin":
        try:
            proc = subprocess.Popen(["caffeinate", "-dims"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    try:
        yield
    finally:
        if sys.platform == "win32":
            set_keep_awake(False)
        elif proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                pass
