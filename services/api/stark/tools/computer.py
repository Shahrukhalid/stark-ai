"""Computer control (desktop app, off until the user turns it on): see an app's buttons and fields, click one,
type text, press a shortcut. Every action asks the user first; the emergency stop blocks all of them.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from ..computer import ControlError, backend, parse_keys
from ..core.tool_registry import ApprovalText, Availability, Risk, Tool, ToolResult

_BACKEND: list[Any] = []


def get_backend():
    if not _BACKEND:
        _BACKEND.append(backend())
    return _BACKEND[0]


class _ControlTool(Tool):
    category = "computer"
    risk = Risk.EXTERNAL  # always needs approval; never covered by a standing grant
    timeout_s = 30

    def availability(self, ctx=None) -> tuple[Availability, str]:
        if ctx is not None and not ctx.settings.desktop:
            return Availability.UNAVAILABLE, "only in the desktop app"
        if ctx is not None and not ctx.settings.computer_control:
            return Availability.UNAVAILABLE, "turned off (Settings → Computer control)"
        return Availability.IMPLEMENTED, ""

    async def _do(self, fn, *a) -> Any:
        try:
            return await asyncio.to_thread(fn, *a)
        except ControlError as exc:
            raise _Failed(str(exc)) from None


class _Failed(Exception):
    pass


class AppInput(BaseModel):
    app: str = Field(min_length=1, max_length=80, description="the app's name, e.g. Notes, Safari, Microsoft Word")


class Elements(_ControlTool):
    id = "computer.elements"
    description = ("List the buttons, fields, menus and links in an open app's window (brings it to the front). "
                   "Use the ids with computer.click. Asks first, because the list shows what is on screen.")
    Input = AppInput
    risk = Risk.READ

    def needs_approval(self, args, ctx) -> bool:
        return True

    def describe(self, args: AppInput, ctx) -> ApprovalText:
        return ApprovalText(f"Look at the buttons and fields in {args.app}", args.app,
                            f"Brings {args.app} to the front and reads the names of its controls.",
                            "The names can include text that is on screen.")

    async def run(self, args: AppInput, ctx) -> ToolResult:
        try:
            items = await self._do(get_backend().elements, args.app)
        except _Failed as exc:
            return ToolResult(False, str(exc))
        return ToolResult(True, f"{len(items)} controls in {args.app}", {"elements": [e.public() for e in items]})


class ClickInput(AppInput):
    element_id: int = Field(ge=1, le=1000)


class Click(_ControlTool):
    id = "computer.click"
    description = "Click a button, link or field (by id from computer.elements) in an open app. Asks first."
    Input = ClickInput

    def describe(self, args: ClickInput, ctx) -> ApprovalText:
        try:
            stamp, items = get_backend()._cache.get(args.app.lower(), (0, {}))
            el = items.get(args.element_id)
        except ControlError:
            el = None
        what = f'the {el.role} "{el.name[:80]}"' if el else f"item {args.element_id}"
        return ApprovalText(f"Click {what} in {args.app}", args.app, f"Clicks {what} in {args.app}.",
                            "A click can change or send something in that app.")

    async def run(self, args: ClickInput, ctx) -> ToolResult:
        try:
            el = await self._do(get_backend().click, args.app, args.element_id)
        except _Failed as exc:
            return ToolResult(False, str(exc))
        return ToolResult(True, f'Clicked the {el.role} "{el.name[:80]}" in {args.app}')


class TypeInput(AppInput):
    text: str = Field(min_length=1, max_length=5000)


class Type(_ControlTool):
    id = "computer.type"
    description = "Type text into the field that has focus in an open app (any language). Asks first."
    Input = TypeInput

    def describe(self, args: TypeInput, ctx) -> ApprovalText:
        return ApprovalText(f"Type in {args.app}", args.app, f"Types: {args.text[:300]}",
                            "The text goes wherever the cursor is in that app.")

    async def run(self, args: TypeInput, ctx) -> ToolResult:
        try:
            await self._do(get_backend().type_text, args.app, args.text)
        except _Failed as exc:
            return ToolResult(False, str(exc))
        return ToolResult(True, f"Typed {len(args.text)} characters in {args.app}")


class KeyInput(AppInput):
    keys: str = Field(min_length=1, max_length=40, description="e.g. cmd+s, ctrl+n, enter, tab")


class Key(_ControlTool):
    id = "computer.key"
    description = "Press a key or shortcut (cmd+s, ctrl+n, enter, tab, …) in an open app. Asks first."
    Input = KeyInput

    def describe(self, args: KeyInput, ctx) -> ApprovalText:
        return ApprovalText(f"Press {args.keys} in {args.app}", args.app, f"Presses {args.keys} in {args.app}.",
                            "Shortcuts can save, send or close things in that app.")

    async def run(self, args: KeyInput, ctx) -> ToolResult:
        try:
            parse_keys(args.keys)
            await self._do(get_backend().key, args.app, args.keys)
        except (ControlError, _Failed) as exc:
            return ToolResult(False, str(exc))
        return ToolResult(True, f"Pressed {args.keys} in {args.app}")


TOOLS = [Elements(), Click(), Type(), Key()]
