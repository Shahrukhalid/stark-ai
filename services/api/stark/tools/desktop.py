"""Everyday help on the user's own computer (desktop app only).

* desktop.status: time, battery, memory, disk, network, the app in front and running apps (read only).
* desktop.open: open a web page, a file in the workspace, or an app (always asks first).
* desktop.notify / desktop.remind: a system notification now or at a set time.
* desktop.clipboard_read / desktop.clipboard_write: always ask first.

Nothing here runs shell commands, changes settings or controls the mouse and keyboard.
"""

from __future__ import annotations

import asyncio
import os
import platform
import re
import subprocess
import sys
import time
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import psutil
from pydantic import BaseModel, Field

from ..core.tool_registry import ApprovalText, Availability, Risk, Tool, ToolResult
from .filesystem import rel, resolve_in_roots

MAC = sys.platform == "darwin"
WIN = os.name == "nt"
APP_NAME = re.compile(r"^[\w .&+'()-]{1,60}$")
REMINDERS = "reminders"


def _run(cmd: list[str], timeout: float = 5, text_in: str | None = None) -> str:
    try:
        r = subprocess.run(cmd, input=text_in, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip()


def front_app() -> str:
    """Name of the app in front (no extra permission needed)."""
    if MAC:
        out = _run(["lsappinfo", "info", "-only", "name", _run(["lsappinfo", "front"])])
        m = re.search(r'"([^"]+)"\s*$', out)
        return m.group(1) if m else ""
    if WIN:
        try:
            import ctypes  # noqa: PLC0415

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            hwnd = user32.GetForegroundWindow()
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(min(n, 300) + 1)
            user32.GetWindowTextW(hwnd, buf, len(buf))
            return buf.value
        except Exception:  # noqa: BLE001
            return ""
    return ""


def running_apps(limit: int = 15) -> list[str]:
    names: dict[str, float] = {}
    me = psutil.Process().username() if hasattr(psutil.Process(), "username") else None
    for p in psutil.process_iter(["name", "username", "memory_info", "exe"]):
        try:
            if me and p.info.get("username") != me:
                continue
            exe = p.info.get("exe") or ""
            if MAC and ".app/Contents/MacOS/" not in exe:
                continue
            if MAC and ("/System/" in exe or "Helper" in exe):
                continue
            name = (re.search(r"/([^/]+)\.app/Contents/MacOS/", exe).group(1) if MAC else p.info["name"]) or ""
            if WIN:
                name = re.sub(r"\.exe$", "", name, flags=re.I)
            mem = p.info["memory_info"].rss if p.info.get("memory_info") else 0
            names[name] = names.get(name, 0) + mem
        except (psutil.Error, AttributeError):
            continue
    return [n for n, _ in sorted(names.items(), key=lambda kv: -kv[1])][:limit]


def notify(title: str, body: str) -> bool:
    """Show a system notification. Returns False where the system offers no simple way."""
    title, body = title[:80], body[:240]
    if MAC:
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
        _run(["osascript", "-e", f'display notification "{esc(body)}" with title "{esc(title)}"'])
        return True
    if WIN:
        script = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
                  "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
                  "$n=$t.GetElementsByTagName('text');$n.Item(0).AppendChild($t.CreateTextNode($env:STARK_T))|Out-Null;"
                  "$n.Item(1).AppendChild($t.CreateTextNode($env:STARK_B))|Out-Null;"
                  "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('STARK AI').Show([Windows.UI.Notifications.ToastNotification]::new($t))")
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", script], env={**os.environ, "STARK_T": title, "STARK_B": body},
                           capture_output=True, timeout=8, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True
        except (OSError, subprocess.SubprocessError):
            return False
    return False


class _DesktopTool(Tool):
    category = "desktop"

    def availability(self, ctx=None) -> tuple[Availability, str]:
        if ctx is not None and not ctx.settings.desktop:
            return Availability.UNAVAILABLE, "only in the desktop app (the engine must run on your computer)"
        return Availability.IMPLEMENTED, ""


class NoInput(BaseModel):
    pass


class Status(_DesktopTool):
    id = "desktop.status"
    description = ("What is happening on the user's computer right now: local time, battery, memory, free disk "
                   "space, whether it is online, the app in front and the apps that are open. Read only.")
    Input = NoInput
    risk = Risk.READ

    async def run(self, args: NoInput, ctx) -> ToolResult:
        return ToolResult(True, "computer status", await asyncio.to_thread(self.collect))

    @staticmethod
    def collect() -> dict[str, Any]:
        vm = psutil.virtual_memory()
        try:
            du = psutil.disk_usage(os.path.expanduser("~"))
            disk = {"free_gb": round(du.free / 1024**3, 1), "used_percent": du.percent}
        except OSError:
            disk = None
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        online = any(s.isup for n, s in psutil.net_if_stats().items() if not n.lower().startswith(("lo", "loopback")))
        now = datetime.now().astimezone()
        return {"local_time": now.strftime("%A %d %B %Y, %H:%M"), "timezone": now.tzname(),
                "system": f"{platform.system()} {platform.release()}",
                "battery": {"percent": round(bat.percent), "charging": bool(bat.power_plugged)} if bat else None,
                "memory_used_percent": vm.percent, "disk": disk, "online": online,
                "front_app": front_app(), "open_apps": running_apps(),
                "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1)}


class OpenInput(BaseModel):
    kind: Literal["url", "file", "app"]
    target: str = Field(min_length=1, max_length=2000, description="https:// address, workspace path, or app name")


class Open(_DesktopTool):
    id = "desktop.open"
    description = ("Open a web page (https), a file or folder in the workspace, or an app by name on the user's "
                   "computer. Always asks the user first.")
    Input = OpenInput
    risk = Risk.EXTERNAL

    def describe(self, args: OpenInput, ctx) -> ApprovalText:
        what = {"url": "web page", "file": "file", "app": "app"}[args.kind]
        return ApprovalText(action=f"Open the {what} {args.target[:200]}", target=args.target[:200],
                            effects=f"Opens the {what} on your computer.",
                            risks="Opening a web page or app you don't know can be unsafe.")

    async def run(self, args: OpenInput, ctx) -> ToolResult:
        t = args.target.strip()
        if args.kind == "url":
            if not re.match(r"^https?://[^\s]+$", t):
                return ToolResult(False, "only http(s) web addresses can be opened")
            await asyncio.to_thread(webbrowser.open, t)
            return ToolResult(True, f"Opened {t}")
        if args.kind == "file":
            try:
                path = resolve_in_roots(t, ctx.settings.workspace_roots)
            except (ValueError, PermissionError) as exc:
                return ToolResult(False, str(exc))
            if not path.exists():
                return ToolResult(False, "file not found in the workspace")
            target = str(path)
            shown = rel(path, ctx.settings.workspace_roots)
        else:
            if not APP_NAME.match(t):
                return ToolResult(False, "not a valid app name")
            target, shown = t, t
        if MAC:
            cmd = ["open", target] if args.kind == "file" else ["open", "-a", target]
            ok = await asyncio.to_thread(lambda: subprocess.run(cmd, capture_output=True, timeout=10, check=False).returncode == 0)
        elif WIN:
            try:
                await asyncio.to_thread(os.startfile, target)  # type: ignore[attr-defined]
                ok = True
            except OSError:
                ok = False
        else:
            ok = await asyncio.to_thread(lambda: subprocess.run(["xdg-open", target], capture_output=True, timeout=10, check=False).returncode == 0)
        return ToolResult(ok, f"Opened {shown}" if ok else f"Could not open {shown}")


class NotifyInput(BaseModel):
    text: str = Field(min_length=1, max_length=240)


class Notify(_DesktopTool):
    id = "desktop.notify"
    description = "Show a short system notification on the user's computer now."
    Input = NotifyInput
    risk = Risk.READ

    async def run(self, args: NotifyInput, ctx) -> ToolResult:
        name = ctx.agent.name if ctx.agent else "STARK AI"
        ok = await asyncio.to_thread(notify, name, args.text)
        ctx.kernel.bus.publish("desktop.notify", {"title": name, "text": args.text})
        return ToolResult(True, "notification shown" if ok else "shown in STARK AI")


class RemindInput(BaseModel):
    text: str = Field(min_length=1, max_length=240)
    minutes: int | None = Field(None, ge=1, le=60 * 24 * 30, description="in this many minutes")
    at: str | None = Field(None, description="or at this local time, ISO format, e.g. 2026-11-08T09:30")


class Remind(_DesktopTool):
    id = "desktop.remind"
    description = ("Set a reminder: a notification on the user's computer in N minutes or at a local time "
                   "(also shown in STARK AI). STARK AI must be running at that time.")
    Input = RemindInput
    risk = Risk.MEMORY

    async def run(self, args: RemindInput, ctx) -> ToolResult:
        if args.minutes:
            when = datetime.now(timezone.utc) + timedelta(minutes=args.minutes)
        elif args.at:
            try:
                when = datetime.fromisoformat(args.at)
            except ValueError:
                return ToolResult(False, "time must look like 2026-11-08T09:30")
            when = (when if when.tzinfo else when.astimezone()).astimezone(timezone.utc)
        else:
            return ToolResult(False, "say when: minutes or a time")
        if when <= datetime.now(timezone.utc):
            return ToolResult(False, "that time has passed")
        items = list(ctx.kernel.db.get_setting(REMINDERS) or [])[-200:]
        item = {"id": uuid.uuid4().hex[:10], "text": args.text, "at": when.isoformat(),
                "agent": ctx.agent.name if ctx.agent else "STARK AI"}
        items.append(item)
        ctx.kernel.db.set_setting(REMINDERS, items)
        local = when.astimezone().strftime("%a %H:%M")
        return ToolResult(True, f"Reminder set for {local}: {args.text}", item)


def due_reminders(kernel, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    items = list(kernel.db.get_setting(REMINDERS) or [])
    due = [i for i in items if datetime.fromisoformat(i["at"]) <= now]
    if due:
        kernel.db.set_setting(REMINDERS, [i for i in items if i not in due])
    return due


async def reminder_loop(kernel) -> None:
    while True:
        try:
            for item in due_reminders(kernel):
                await asyncio.to_thread(notify, item.get("agent", "STARK AI"), item["text"])
                kernel.bus.publish("desktop.notify", {"title": item.get("agent", "STARK AI"), "text": item["text"]})
        except Exception:  # noqa: BLE001 - never stop the loop
            pass
        await asyncio.sleep(20)


class ClipRead(_DesktopTool):
    id = "desktop.clipboard_read"
    description = "Read the text on the user's clipboard. Always asks the user first."
    Input = NoInput
    risk = Risk.READ

    def needs_approval(self, args, ctx) -> bool:
        return True  # the clipboard often holds private things

    def describe(self, args, ctx) -> ApprovalText:
        return ApprovalText("Read your clipboard", "clipboard", "The assistant sees the text you last copied.",
                            "It may contain private information.")

    async def run(self, args: NoInput, ctx) -> ToolResult:
        if MAC:
            text = await asyncio.to_thread(_run, ["pbpaste"])
        elif WIN:
            text = await asyncio.to_thread(_run, ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"])
        else:
            return ToolResult(False, "clipboard not supported here")
        return ToolResult(True, f"{len(text)} characters", {"text": text[:8000]})


class ClipWriteInput(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class ClipWrite(_DesktopTool):
    id = "desktop.clipboard_write"
    description = "Copy text to the user's clipboard so they can paste it. Asks first."
    Input = ClipWriteInput
    risk = Risk.WRITE

    def describe(self, args: ClipWriteInput, ctx) -> ApprovalText:
        return ApprovalText("Copy text to your clipboard", "clipboard", f"Replaces your clipboard with: {args.text[:120]}",
                            "Whatever you copied before is replaced.")

    async def run(self, args: ClipWriteInput, ctx) -> ToolResult:
        if MAC:
            await asyncio.to_thread(_run, ["pbcopy"], 5, args.text)
        elif WIN:
            await asyncio.to_thread(_run, ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"], 8, args.text)
        else:
            return ToolResult(False, "clipboard not supported here")
        return ToolResult(True, "copied to the clipboard")


TOOLS = [Status(), Open(), Notify(), Remind(), ClipRead(), ClipWrite()]
