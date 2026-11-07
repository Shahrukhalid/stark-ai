"""Computer control: off by default, desktop only, every action asks first, shortcuts are validated."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stark.computer import Backend, ControlError, Element, parse_keys
from stark.core.permissions import ALWAYS_APPROVE
from stark.core.tool_registry import Availability
from stark.tools import computer


class Fake(Backend):
    def __init__(self):
        super().__init__()
        self.log = []

    def trusted(self, prompt=False):
        return True

    def activate(self, app):
        self.log.append(("activate", app))
        return app

    def _elements(self, app):
        return [Element(1, "button", "Send", 10, 10), Element(2, "text field", "To", 5, 5)]

    def _press(self, el):
        self.log.append(("press", el.name))

    def type_text(self, app, text):
        self.log.append(("type", text))

    def key(self, app, combo):
        self.log.append(("key", combo))


def test_shortcuts_are_validated():
    assert parse_keys("Cmd+Shift+S") == (["cmd", "shift"], "s")
    assert parse_keys("enter") == ([], "enter")
    for bad in ("cmd+", "hyper+s", "rm -rf", ""):
        with pytest.raises(ControlError):
            parse_keys(bad)


def test_off_by_default_and_desktop_only(settings):
    ctx = SimpleNamespace(settings=settings)
    settings.desktop, settings.computer_control = False, True
    assert computer.Click().availability(ctx)[0] is Availability.UNAVAILABLE
    settings.desktop, settings.computer_control = True, False
    assert "turned off" in computer.Click().availability(ctx)[1]
    settings.computer_control = True
    assert computer.Click().availability(ctx)[0] is Availability.IMPLEMENTED


def test_every_action_asks_first():
    for t in (computer.Click, computer.Type, computer.Key):
        assert t.risk in ALWAYS_APPROVE
    assert computer.Elements().needs_approval(None, None) is True


async def test_list_click_type_and_keys(monkeypatch, settings):
    fake = Fake()
    monkeypatch.setattr(computer, "_BACKEND", [fake])
    ctx = SimpleNamespace(settings=settings)
    r = await computer.Elements().run(computer.AppInput(app="Mail"), ctx)
    assert r.data["elements"][0] == {"id": 1, "role": "button", "name": "Send"}
    text = computer.Click().describe(computer.ClickInput(app="Mail", element_id=1), ctx)
    assert text.action == 'Click the button "Send" in Mail'
    assert (await computer.Click().run(computer.ClickInput(app="Mail", element_id=1), ctx)).ok
    assert not (await computer.Click().run(computer.ClickInput(app="Notes", element_id=1), ctx)).ok  # not listed
    assert (await computer.Type().run(computer.TypeInput(app="Mail", text="سلام"), ctx)).ok
    assert not (await computer.Key().run(computer.KeyInput(app="Mail", keys="cmd+hyper"), ctx)).ok
    assert (await computer.Key().run(computer.KeyInput(app="Mail", keys="cmd+enter"), ctx)).ok
    assert ("press", "Send") in fake.log and ("type", "سلام") in fake.log and ("key", "cmd+enter") in fake.log


def test_status_outside_the_desktop_app(settings):
    from fastapi.testclient import TestClient

    from stark.main import create_app

    with TestClient(create_app(settings, {})) as c:
        c.headers["authorization"] = f"Bearer {settings.api_token}"
        assert c.get("/api/computer/status").json() == {"available": False, "enabled": False, "allowed": False}
        assert c.post("/api/computer/allow").status_code == 409
