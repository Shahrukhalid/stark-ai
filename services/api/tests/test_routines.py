"""Routines by voice, quiet hours and the daily summary."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from stark import routines
from stark.main import create_app
from stark.tools import routines as rt


def _client(settings):
    c = TestClient(create_app(settings, {}))
    c.__enter__()
    c.headers["authorization"] = f"Bearer {settings.api_token}"
    return c


def test_quiet_hours_across_midnight(settings):
    c = _client(settings)
    k = c.app.state.kernel
    k.db.set_setting(routines.QUIET, {"enabled": True, "start": "22:00", "end": "07:00"})
    at = lambda h: datetime(2026, 11, 8, h, 0).astimezone()  # noqa: E731
    assert routines.quiet_now(k, at(23)) and routines.quiet_now(k, at(3)) and not routines.quiet_now(k, at(12))
    k.db.set_setting(routines.QUIET, {"enabled": False, "start": "22:00", "end": "07:00"})
    assert not routines.quiet_now(k, at(23))
    c.__exit__(None, None, None)


async def test_wanda_creates_a_weekly_routine(settings):
    c = _client(settings)
    k = c.app.state.kernel
    ctx = SimpleNamespace(kernel=k, agent=SimpleNamespace(id="wanda"))
    args = rt.RoutineInput(name="Monday ideas", every="week", weekday="monday", time="09:00", task="Draft three posts")
    assert rt.CreateRoutine().describe(args, ctx).action == "Create routine 'Monday ideas' (every Monday at 09:00)"
    r = await rt.CreateRoutine().run(args, ctx)
    assert r.ok
    (a,) = k.automations.list()
    assert a["trigger"] == {"type": "schedule", "every": "week", "time": "09:00", "weekday": 0}
    assert a["actions"] == [{"type": "ask_agent", "agent": "wanda", "message": "Draft three posts"}]
    assert (await rt.ListRoutines().run(rt.NoInput(), ctx)).data["automations"][0]["name"] == "Monday ideas"
    bad = await rt.CreateRoutine().run(rt.RoutineInput(name="x", every="week", time="09:00", task="abc"), ctx)
    assert not bad.ok
    c.__exit__(None, None, None)


def test_summary_settings_and_text(settings):
    c = _client(settings)
    r = c.put("/api/assistant/routines", json={"quiet": {"enabled": True, "start": "23:00", "end": "06:30"},
                                               "summary": {"enabled": True, "time": "08:45", "agent": "wanda", "language": "ur"}})
    assert r.status_code == 200 and r.json()["summary"]["agent"] == "wanda"
    assert c.put("/api/assistant/routines", json={"quiet": {"start": "25:00"}, "summary": {}}).status_code == 422
    out = c.post("/api/assistant/summary").json()
    assert "پوسٹس" in out["text"] and out["spoken"] is False  # no voice in tests
    k = c.app.state.kernel
    assert routines.summary_due(k, datetime(2026, 11, 8, 9, 0).astimezone())
    assert not routines.summary_due(k, datetime(2026, 11, 8, 8, 0).astimezone())
    assert "Nothing needs you right now" in routines.summary_text(k)
    c.__exit__(None, None, None)
