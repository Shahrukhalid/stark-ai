"""Everyday desktop help: status, open (with approval), reminders and clipboard (with approval)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from stark.core.tool_registry import Availability, Risk
from stark.tools import desktop


class _DB:
    def __init__(self):
        self.s = {}

    def get_setting(self, k, default=None):
        return self.s.get(k, default)

    def set_setting(self, k, v):
        self.s[k] = v


def _ctx(settings, desktop_mode=True):
    settings.desktop = desktop_mode
    events = []
    kernel = SimpleNamespace(db=_DB(), bus=SimpleNamespace(publish=lambda t, d: events.append((t, d))))
    return SimpleNamespace(settings=settings, kernel=kernel, agent=SimpleNamespace(name="VISION", id="vision")), events


def test_only_in_the_desktop_app(settings):
    ctx, _ = _ctx(settings, False)
    assert desktop.Status().availability(ctx)[0] is Availability.UNAVAILABLE
    ctx, _ = _ctx(settings, True)
    assert desktop.Status().availability(ctx)[0] is Availability.IMPLEMENTED


async def test_status_reports_the_computer(settings):
    ctx, _ = _ctx(settings)
    r = await desktop.Status().run(desktop.NoInput(), ctx)
    assert r.ok and {"local_time", "memory_used_percent", "online", "open_apps"} <= set(r.data)


def test_risky_tools_always_ask():
    assert desktop.Open.risk is Risk.EXTERNAL and desktop.ClipWrite.risk is Risk.WRITE
    assert desktop.ClipRead().needs_approval(None, None) is True
    text = desktop.Open().describe(desktop.OpenInput(kind="url", target="https://example.org"), None)
    assert "example.org" in text.action


async def test_open_refuses_anything_but_web_pages_workspace_files_and_app_names(settings, monkeypatch):
    ctx, _ = _ctx(settings)
    opened = []
    monkeypatch.setattr(desktop.webbrowser, "open", lambda u: opened.append(u))
    assert (await desktop.Open().run(desktop.OpenInput(kind="url", target="javascript:alert(1)"), ctx)).ok is False
    assert (await desktop.Open().run(desktop.OpenInput(kind="url", target="https://example.org/a"), ctx)).ok
    assert opened == ["https://example.org/a"]
    assert (await desktop.Open().run(desktop.OpenInput(kind="app", target="rm -rf /; echo"), ctx)).ok is False
    assert (await desktop.Open().run(desktop.OpenInput(kind="file", target="../../etc/passwd"), ctx)).ok is False


async def test_reminders_fire_once(settings, monkeypatch):
    ctx, events = _ctx(settings)
    r = await desktop.Remind().run(desktop.RemindInput(text="Post the launch thread", minutes=5), ctx)
    assert r.ok and "Post the launch thread" in r.summary
    assert desktop.due_reminders(ctx.kernel) == []
    later = datetime.now(timezone.utc) + timedelta(minutes=6)
    due = desktop.due_reminders(ctx.kernel, later)
    assert [d["text"] for d in due] == ["Post the launch thread"] and desktop.due_reminders(ctx.kernel, later) == []
    assert (await desktop.Remind().run(desktop.RemindInput(text="x", at="2001-01-01T09:00"), ctx)).ok is False
