"""macOS backend: Accessibility API (AX) and Quartz events, through PyObjC (bundled with the desktop app)."""

from __future__ import annotations

import time

from . import Backend, ControlError, Element, parse_keys

ROLES = {"AXButton": "button", "AXTextField": "text field", "AXTextArea": "text area", "AXCheckBox": "checkbox",
         "AXRadioButton": "option", "AXMenuItem": "menu item", "AXLink": "link", "AXPopUpButton": "menu",
         "AXComboBox": "combo box", "AXTab": "tab", "AXMenuButton": "menu", "AXSearchField": "search field"}
KEYCODES = {"enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "delete": 117, "escape": 53,
            "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126, "home": 115, "end": 119, "pageup": 116,
            "pagedown": 121, "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
            "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
            "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38,
            "k": 40, "n": 45, "m": 46, "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
            "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111}


class MacBackend(Backend):
    def __init__(self) -> None:
        super().__init__()
        try:
            import AppKit  # noqa: F401
            import ApplicationServices  # noqa: F401
            import Quartz  # noqa: F401
        except ImportError as exc:
            raise ControlError("Computer control is not available in this build.") from exc

    def trusted(self, prompt: bool = False) -> bool:
        import ApplicationServices as AS

        if prompt:
            return bool(AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True}))
        return bool(AS.AXIsProcessTrusted())

    def _app(self, name: str):
        from AppKit import NSWorkspace

        want = name.lower().strip()
        apps = [a for a in NSWorkspace.sharedWorkspace().runningApplications() if a.localizedName()]
        for a in apps:
            if a.localizedName().lower() == want:
                return a
        for a in apps:
            if want in a.localizedName().lower() and a.activationPolicy() == 0:
                return a
        raise ControlError(f"{name} is not open. Open it first.")

    def activate(self, app: str) -> str:
        a = self._app(app)
        a.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps
        time.sleep(0.35)
        return a.localizedName()

    def _elements(self, app: str) -> list[Element]:
        import ApplicationServices as AS

        a = self._app(app)
        root = AS.AXUIElementCreateApplication(a.processIdentifier())
        err, win = AS.AXUIElementCopyAttributeValue(root, "AXFocusedWindow", None)
        if err or win is None:
            raise ControlError(f"{app} has no window open.")
        found: list[Element] = []

        def attr(el, name):
            e, v = AS.AXUIElementCopyAttributeValue(el, name, None)
            return None if e else v

        def walk(el, depth: int) -> None:
            if depth > 12 or len(found) >= 150:
                return
            role = attr(el, "AXRole") or ""
            if role in ROLES:
                label = attr(el, "AXTitle") or attr(el, "AXDescription") or attr(el, "AXValue") or attr(el, "AXHelp") or ""
                pos, size = attr(el, "AXPosition"), attr(el, "AXSize")
                x = y = 0
                if pos is not None and size is not None:
                    ok_p, p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
                    ok_s, s = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
                    if ok_p and ok_s:
                        x, y = int(p.x + s.width / 2), int(p.y + s.height / 2)
                found.append(Element(len(found) + 1, ROLES[role], str(label), x, y, el))
            for child in attr(el, "AXChildren") or []:
                walk(child, depth + 1)

        walk(win, 0)
        return found

    def _press(self, el: Element) -> None:
        import ApplicationServices as AS
        import Quartz as Q

        if el.ref is not None and AS.AXUIElementPerformAction(el.ref, "AXPress") == 0:
            return
        if not (el.x or el.y):
            raise ControlError("That item can't be clicked.")
        for kind in (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp):
            Q.CGEventPost(Q.kCGHIDEventTap, Q.CGEventCreateMouseEvent(None, kind, (el.x, el.y), Q.kCGMouseButtonLeft))
            time.sleep(0.03)

    def type_text(self, app: str, text: str) -> None:
        import Quartz as Q

        self.activate(app)
        for i in range(0, len(text), 16):  # Unicode text, any language
            chunk = text[i:i + 16]
            for down in (True, False):
                ev = Q.CGEventCreateKeyboardEvent(None, 0, down)
                Q.CGEventKeyboardSetUnicodeString(ev, len(chunk), chunk)
                Q.CGEventPost(Q.kCGHIDEventTap, ev)
            time.sleep(0.01)

    def key(self, app: str, combo: str) -> None:
        import Quartz as Q

        mods, key = parse_keys(combo)
        flags = 0
        for m in mods:
            flags |= {"cmd": Q.kCGEventFlagMaskCommand, "command": Q.kCGEventFlagMaskCommand,
                      "ctrl": Q.kCGEventFlagMaskControl, "control": Q.kCGEventFlagMaskControl,
                      "alt": Q.kCGEventFlagMaskAlternate, "option": Q.kCGEventFlagMaskAlternate,
                      "shift": Q.kCGEventFlagMaskShift}[m]
        if key not in KEYCODES:
            raise ControlError(f"the key {key!r} isn't supported")
        self.activate(app)
        for down in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(None, KEYCODES[key], down)
            Q.CGEventSetFlags(ev, flags)
            Q.CGEventPost(Q.kCGHIDEventTap, ev)
