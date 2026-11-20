"""2.2: one-click Mastodon sign-in, system-browser OAuth and self-closing callback pages."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from stark import oauth


def _mastodon(seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/api/v1/apps":
            form = parse_qs(req.content.decode())
            assert form["redirect_uris"][0].startswith("http://127.0.0.1:") and "write:statuses" in form["scopes"][0]
            return httpx.Response(200, json={"client_id": "cid", "client_secret": "csec"})
        if req.url.path == "/oauth/token":
            form = parse_qs(req.content.decode())
            assert form["code"] == ["the-code"] and form["code_verifier"][0] and form["client_secret"] == ["csec"]
            return httpx.Response(200, json={"access_token": "mtok", "token_type": "Bearer"})
        if req.url.path == "/api/v1/accounts/verify_credentials":
            assert req.headers["authorization"] == "Bearer mtok"
            return httpx.Response(200, json={"acct": "owner"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_mastodon_registers_itself_and_uses_pkce():
    seen: list = []
    url = urlparse(await oauth.mastodon_start("mastodon.test", 8123, _mastodon(seen)))
    q = parse_qs(url.query)
    assert url.netloc == "mastodon.test" and url.path == "/oauth/authorize"
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["cid"]
    state = q["state"][0]
    assert oauth.is_mastodon(state) and not oauth.is_official(state)
    assert await oauth.mastodon_finish(state, "the-code", _mastodon(seen)) == ("https://mastodon.test", "mtok")
    with pytest.raises(oauth.OAuthError, match="expired"):  # single use
        await oauth.mastodon_finish(state, "the-code", _mastodon(seen))
    with pytest.raises(oauth.OAuthError, match="https"):
        await oauth.mastodon_start("http://mastodon.test", 8123, _mastodon(seen))


async def test_mastodon_server_that_refuses_is_explained():
    t = httpx.MockTransport(lambda r: httpx.Response(422, json={"error": "nope"}))
    with pytest.raises(oauth.OAuthError, match="did not accept"):
        await oauth.mastodon_start("mastodon.test", 8123, t)


def test_only_https_pages_open_in_the_system_browser(monkeypatch):
    opened: list[str] = []
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda u, new=0: opened.append(u) or True)
    assert oauth.open_in_system_browser("https://accounts.google.com/x") is True
    assert oauth.open_in_system_browser("file:///etc/passwd") is False
    assert oauth.open_in_system_browser("javascript:alert(1)") is False
    assert opened == ["https://accounts.google.com/x"]


@pytest.fixture
def client(settings, monkeypatch):
    from stark.main import create_app
    from stark.tools import social as social_mod
    from stark.tools import web

    async def ok(url, allow_private):
        return None

    monkeypatch.setattr(web, "check_url", ok)
    seen: list = []
    real_init = social_mod.Social.__init__

    def init(self, s, transport=None):
        real_init(self, s, _mastodon(seen))

    monkeypatch.setattr(social_mod.Social, "__init__", init)
    with TestClient(create_app(settings, {})) as c:
        c.headers["authorization"] = f"Bearer {settings.api_token}"
        yield c


def test_mastodon_one_click_end_to_end(client, monkeypatch):
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: pytest.fail("browser must not open outside the desktop app"))
    r = client.post("/api/oauth/mastodon/login?open=1", json={"base_url": "mastodon.test"})
    assert r.status_code == 200 and r.json()["opened"] is False
    state = parse_qs(urlparse(r.json()["auth_url"]).query)["state"][0]
    page = client.get(f"/api/oauth/mastodon/callback?state={state}&code=the-code")
    assert page.status_code == 200 and "@owner@mastodon.test" in page.text and "window.close" in page.text
    assert page.headers["cache-control"] == "no-store"
    acc = {a["platform"]: a for a in client.get("/api/social/accounts").json()["accounts"]}["mastodon"]
    assert acc["mode"] == "connected"
    replay = client.get(f"/api/oauth/mastodon/callback?state={state}&code=the-code")
    assert replay.status_code == 400 and "window.close" not in replay.text
    assert TestClient(client.app).post("/api/oauth/mastodon/login", json={"base_url": "x"}).status_code == 401


def test_desktop_opens_sign_in_in_the_system_browser(client, settings, monkeypatch):
    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda u, new=0: opened.append(u) or True)
    settings.desktop = True
    r = client.post("/api/oauth/mastodon/login?open=1", json={"base_url": "mastodon.test"}).json()
    assert r["opened"] is True and opened == [r["auth_url"]]
