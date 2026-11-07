"""Computer control for the desktop app: list the buttons and fields of an app's window, click one, type text,
press a shortcut. Every action is approved by the user in STARK AI first (see tools/computer.py).

macOS uses the Accessibility API (the user allows STARK AI once in System Settings → Privacy & Security →
Accessibility); Windows uses UI Automation and SendInput. Nothing here runs commands or changes settings.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Any


class ControlError(RuntimeError):
    """Safe to show to the user."""


@dataclass
class Element:
    id: int
    role: str
    name: str
    x: int
    y: int
    ref: Any = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "role": self.role, "name": self.name[:120]}


KEYS = {"enter", "return", "tab", "escape", "esc", "space", "backspace", "delete", "up", "down", "left", "right",
        "home", "end", "pageup", "pagedown", *"abcdefghijklmnopqrstuvwxyz", *"0123456789",
        *(f"f{i}" for i in range(1, 13))}
MODS = {"cmd", "command", "ctrl", "control", "alt", "option", "shift"}


def parse_keys(combo: str) -> tuple[list[str], str]:
    """'cmd+shift+s' -> (['cmd', 'shift'], 's'). Raises ControlError for anything unknown."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts or parts[-1] not in KEYS or any(p not in MODS for p in parts[:-1]):
        raise ControlError(f"unknown shortcut {combo!r}; use e.g. cmd+s, ctrl+shift+n, enter")
    return parts[:-1], parts[-1]


class Backend:
    """Platform backend; the cache keeps the last listed elements for two minutes."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict[int, Element]]] = {}

    # implemented by the platform classes
    def trusted(self, prompt: bool = False) -> bool: ...
    def activate(self, app: str) -> str: ...
    def _elements(self, app: str) -> list[Element]: ...
    def _press(self, el: Element) -> None: ...
    def type_text(self, app: str, text: str) -> None: ...
    def key(self, app: str, combo: str) -> None: ...

    def elements(self, app: str) -> list[Element]:
        if not self.trusted():
            raise ControlError("Allow STARK AI to control the computer first (Settings → Computer control).")
        name = self.activate(app)
        found = self._elements(name)
        self._cache[app.lower()] = (time.time(), {e.id: e for e in found})
        return found

    def click(self, app: str, element_id: int) -> Element:
        stamp, items = self._cache.get(app.lower(), (0.0, {}))
        if time.time() - stamp > 120 or element_id not in items:
            raise ControlError("List the app's buttons again first (they may have changed).")
        el = items[element_id]
        self.activate(app)
        self._press(el)
        return el


def backend() -> Backend:
    if sys.platform == "darwin":
        from .mac import MacBackend

        return MacBackend()
    if sys.platform == "win32":
        from .win import WinBackend

        return WinBackend()
    raise ControlError("Computer control works on macOS and Windows.")
