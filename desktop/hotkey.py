"""A shortcut that brings STARK AI forward from any app: Option+Shift+Space (macOS) / Alt+Shift+Space (Windows).

macOS uses the Carbon hot-key service, which needs no extra permission; Windows uses RegisterHotKey on its own
thread. If the shortcut is taken by another app, nothing happens and STARK AI works as before.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from typing import Callable

LABEL = "⌥⇧Space" if sys.platform == "darwin" else "Alt+Shift+Space"
_keep: list[object] = []  # callbacks must stay alive for the life of the app


def register(callback: Callable[[], None]) -> bool:
    try:
        if sys.platform == "darwin":
            return _mac(callback)
        if sys.platform == "win32":
            return _windows(callback)
    except Exception:  # noqa: BLE001 - a missing shortcut must never stop the app
        return False
    return False


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode("ascii"), "big")


def _mac(callback: Callable[[], None]) -> bool:
    carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")

    class EventTypeSpec(ctypes.Structure):
        _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]

    class EventHotKeyID(ctypes.Structure):
        _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]

    handler_t = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

    def on_event(_call, _event, _data) -> int:
        threading.Thread(target=callback, daemon=True).start()  # never block the main thread
        return 0

    handler = handler_t(on_event)
    carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
    target = carbon.GetApplicationEventTarget()
    spec = EventTypeSpec(_fourcc("keyb"), 5)  # kEventClassKeyboard, kEventHotKeyPressed
    carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, handler_t, ctypes.c_uint32, ctypes.POINTER(EventTypeSpec),
                                           ctypes.c_void_p, ctypes.c_void_p]
    if carbon.InstallEventHandler(target, handler, 1, ctypes.byref(spec), None, None) != 0:
        return False
    ref = ctypes.c_void_p()
    carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID, ctypes.c_void_p,
                                           ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    ok = carbon.RegisterEventHotKey(49, 2048 | 512, EventHotKeyID(_fourcc("STRK"), 1), target, 0, ctypes.byref(ref)) == 0
    _keep.extend([handler, spec, ref])  # space, optionKey | shiftKey
    return ok


def _windows(callback: Callable[[], None]) -> bool:
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    ready = threading.Event()
    result: list[bool] = []

    def loop() -> None:
        ok = bool(user32.RegisterHotKey(None, 1, 0x0001 | 0x0004 | 0x4000, 0x20))  # Alt | Shift | NoRepeat, Space
        result.append(ok)
        ready.set()
        if not ok:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                threading.Thread(target=callback, daemon=True).start()

    threading.Thread(target=loop, name="stark-hotkey", daemon=True).start()
    ready.wait(3)
    return bool(result and result[0])
