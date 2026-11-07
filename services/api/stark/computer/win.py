"""Windows backend: UI Automation (comtypes, bundled) to list and press controls; SendInput to type and press keys."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from . import Backend, ControlError, Element, parse_keys

user32 = ctypes.windll.user32  # type: ignore[attr-defined]
TYPES = {50000: "button", 50004: "text field", 50002: "checkbox", 50013: "option", 50011: "menu item",
         50005: "link", 50003: "combo box", 50019: "tab", 50007: "list item", 50031: "split button"}
VK = {"enter": 0x0D, "return": 0x0D, "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "escape": 0x1B,
      "esc": 0x1B, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "home": 0x24, "end": 0x23,
      "pageup": 0x21, "pagedown": 0x22, **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"},
      **{f"f{i}": 0x6F + i for i in range(1, 13)}}
MODVK = {"ctrl": 0x11, "control": 0x11, "cmd": 0x11, "command": 0x11, "alt": 0x12, "option": 0x12, "shift": 0x10}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(vk: int = 0, scan: int = 0, flags: int = 0) -> None:
    inp = INPUT(type=1)
    inp.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


class WinBackend(Backend):
    def __init__(self) -> None:
        super().__init__()
        try:
            import comtypes.client  # noqa: F401
        except ImportError as exc:
            raise ControlError("Computer control is not available in this build.") from exc
        self._uia = None

    def trusted(self, prompt: bool = False) -> bool:
        return True  # Windows needs no extra permission

    def _window(self, app: str) -> int:
        want = app.lower().strip()
        found: list[int] = []
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if want and want in buf.value.lower():
                    found.append(hwnd)
            return True

        user32.EnumWindows(proto(cb), 0)
        if not found:
            raise ControlError(f"No open window matches {app}. Open it first.")
        return found[0]

    def activate(self, app: str) -> str:
        hwnd = self._window(app)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        _send(0x12)  # a tap of Alt lets this process bring the window forward
        _send(0x12, flags=2)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        return app

    def _automation(self):
        if self._uia is None:
            import comtypes.client

            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as U

            self._uia = (comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation), U)
        return self._uia

    def _elements(self, app: str) -> list[Element]:
        uia, U = self._automation()
        root = uia.ElementFromHandle(self._window(app))
        items = root.FindAll(U.TreeScope_Descendants, uia.CreateTrueCondition())
        found: list[Element] = []
        for i in range(min(items.Length, 600)):
            el = items.GetElement(i)
            t = el.CurrentControlType
            if t not in TYPES or not el.CurrentIsEnabled or el.CurrentIsOffscreen:
                continue
            r = el.CurrentBoundingRectangle
            found.append(Element(len(found) + 1, TYPES[t], el.CurrentName or "", (r.left + r.right) // 2,
                                 (r.top + r.bottom) // 2, el))
            if len(found) >= 150:
                break
        return found

    def _press(self, el: Element) -> None:
        uia, U = self._automation()
        try:
            pattern = el.ref.GetCurrentPattern(U.UIA_InvokePatternId)
            if pattern:
                pattern.QueryInterface(U.IUIAutomationInvokePattern).Invoke()
                return
        except Exception:  # noqa: BLE001 - fall back to a mouse click
            pass
        user32.SetCursorPos(el.x, el.y)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)

    def type_text(self, app: str, text: str) -> None:
        self.activate(app)
        for ch in text:  # Unicode text, any language
            code = ord(ch)
            _send(scan=code, flags=0x0004)
            _send(scan=code, flags=0x0004 | 0x0002)

    def key(self, app: str, combo: str) -> None:
        mods, key = parse_keys(combo)
        if key not in VK:
            raise ControlError(f"the key {key!r} isn't supported")
        self.activate(app)
        for m in mods:
            _send(MODVK[m])
        _send(VK[key])
        _send(VK[key], flags=2)
        for m in reversed(mods):
            _send(MODVK[m], flags=2)
