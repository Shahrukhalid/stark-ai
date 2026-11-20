"""Sign-in (OAuth 2.0 with PKCE) for platforms whose access tokens expire quickly: YouTube and TikTok.

The owner uses their own developer app (client ID and secret). STARK opens the platform's sign-in page,
the platform redirects back to this computer (http://127.0.0.1:<port>/api/oauth/<platform>/callback),
and only the long-lived refresh token is stored, in the local account store.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx

PROVIDERS: dict[str, dict[str, Any]] = {
    "youtube": {"auth": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
                "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
                "id_param": "client_id", "pkce": "b64",
                "fields": ("youtube_client_id", "youtube_client_secret", "youtube_refresh_token"),
                "extra": {"access_type": "offline", "prompt": "consent"}},
    "tiktok": {"auth": "https://www.tiktok.com/v2/auth/authorize/", "token": "https://open.tiktokapis.com/v2/oauth/token/",
               "scope": "user.info.basic,video.publish,video.upload", "id_param": "client_key", "pkce": "hex",
               "fields": ("tiktok_client_key", "tiktok_client_secret", "tiktok_refresh_token"), "extra": {}},
}
PENDING: dict[str, dict[str, Any]] = {}
TTL_S = 900


class OAuthError(RuntimeError):
    pass


def redirect_uri(port: int, platform: str) -> str:
    return f"http://127.0.0.1:{port}/api/oauth/{platform}/callback"


def start(platform: str, client_id: str, client_secret: str, port: int, extra_scope: str = "") -> str:
    p = PROVIDERS[platform]
    if not client_id.strip() or not client_secret.strip():
        raise OAuthError("Enter the client ID and secret of your developer app first.")
    now = time.time()
    for k in [k for k, v in PENDING.items() if v["created"] < now - TTL_S]:
        PENDING.pop(k, None)
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = digest.hex() if p["pkce"] == "hex" else base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    redirect = redirect_uri(port, platform)
    PENDING[state] = {"platform": platform, "client_id": client_id.strip(), "client_secret": client_secret.strip(),
                      "verifier": verifier, "redirect": redirect, "created": now}
    sep = "," if "," in p["scope"] else " "
    scope = p["scope"] + (sep + extra_scope if extra_scope else "")
    q = {p["id_param"]: client_id.strip(), "redirect_uri": redirect, "response_type": "code", "scope": scope,
         "state": state, "code_challenge": challenge, "code_challenge_method": "S256", **p["extra"]}
    return f"{p['auth']}?{urlencode(q)}"


async def finish(platform: str, state: str, code: str, transport=None) -> dict[str, str]:
    """Exchange the code for tokens; returns the setting values to store."""
    pending = PENDING.pop(state, None)
    if not pending or pending["platform"] != platform or pending["created"] < time.time() - TTL_S:
        raise OAuthError("This sign-in link expired or was already used. Start again from Your accounts.")
    p = PROVIDERS[platform]
    body = {p["id_param"]: pending["client_id"], "client_secret": pending["client_secret"], "code": code,
            "grant_type": "authorization_code", "redirect_uri": pending["redirect"], "code_verifier": pending["verifier"]}
    async with httpx.AsyncClient(timeout=30, transport=transport) as c:
        r = await c.post(p["token"], data=body)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or not data.get("refresh_token"):
        raise OAuthError("The platform did not return a refresh token "
                         f"({data.get('error_description') or data.get('error') or r.status_code}).")
    cid, secret, refresh = p["fields"]
    return {cid: pending["client_id"], secret: pending["client_secret"], refresh: data["refresh_token"]}


# ---------------------------------------------------------------- official apps through STARK Connect
# "Log in with …" uses Nexonic's registered apps. Their secrets live on the STARK Connect server
# (services/connect), which sends the browser back to this computer and swaps the code for tokens.
OFFICIAL = ("facebook", "instagram", "threads", "linkedin", "tiktok", "youtube", "pinterest")
_PKCE = {"tiktok": "hex", "youtube": "b64"}


def official_start(connect_url: str, platform: str, port: int) -> str:
    if not connect_url:
        raise OAuthError("Log in is not set up in this copy of STARK AI (STARK_CONNECT_URL is empty).")
    now = time.time()
    for k in [k for k, v in PENDING.items() if v["created"] < now - TTL_S]:
        PENDING.pop(k, None)
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    kind = _PKCE.get(platform)
    challenge = digest.hex() if kind == "hex" else base64.urlsafe_b64encode(digest).rstrip(b"=").decode() if kind else ""
    state = secrets.token_urlsafe(24)
    PENDING[state] = {"platform": platform, "verifier": verifier, "official": True, "created": now}
    q = {"return": redirect_uri(port, platform), "state": state}
    if challenge:
        q["challenge"] = challenge
    return f"{connect_url.rstrip('/')}/start/{platform}?{urlencode(q)}"


def is_official(state: str) -> bool:
    return bool(PENDING.get(state, {}).get("official"))


async def official_finish(connect_url: str, platform: str, state: str, code: str, transport=None) -> dict[str, Any]:
    pending = PENDING.pop(state, None)
    if not pending or pending["platform"] != platform or pending["created"] < time.time() - TTL_S:
        raise OAuthError("This sign-in link expired or was already used. Start again from Your accounts.")
    async with httpx.AsyncClient(timeout=30, transport=transport) as c:
        r = await c.post(f"{connect_url.rstrip('/')}/token/{platform}",
                         json={"code": code, "code_verifier": pending["verifier"]})
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or not data.get("access_token"):
        raise OAuthError(f"Sign-in failed: {data.get('error') or r.status_code}")
    return data


# Choices to make after sign-in (which Facebook Page, which Pinterest board), single use.
PICKS: dict[str, dict[str, Any]] = {}


def remember_pick(platform: str, options: list[dict[str, str]]) -> str:
    token = secrets.token_urlsafe(18)
    PICKS[token] = {"platform": platform, "options": options, "created": time.time()}
    return token


def take_pick(platform: str, token: str, choice: str) -> dict[str, str]:
    entry = PICKS.pop(token, None)
    if not entry or entry["platform"] != platform or entry["created"] < time.time() - TTL_S:
        raise OAuthError("This choice expired. Log in again from Your accounts.")
    opt = next((o for o in entry["options"] if o["id"] == choice), None)
    if not opt:
        raise OAuthError("Unknown choice.")
    return opt


# ---------------------------------------------------------------- Mastodon (any server, no developer app needed)
# Mastodon lets every client register itself on the user's server (POST /api/v1/apps), so "Log in with Mastodon"
# needs no Nexonic app and no review: STARK registers on the chosen server, the browser signs in there, and the
# server sends it back to this computer. Mastodon access tokens do not expire.
MASTODON_SCOPES = "read write:statuses write:media"


async def mastodon_start(base_url: str, port: int, transport=None) -> str:
    base = base_url.strip().rstrip("/")
    if not base:
        raise OAuthError("Enter your Mastodon server, for example mastodon.social.")
    if "://" not in base:
        base = "https://" + base
    if not base.startswith("https://"):
        raise OAuthError("The server address must start with https://")
    base = server_origin(base)
    redirect = redirect_uri(port, "mastodon")
    async with httpx.AsyncClient(timeout=20, transport=transport, follow_redirects=False) as c:
        r = await c.post(f"{base}/api/v1/apps", data={"client_name": "STARK AI", "redirect_uris": redirect,
                                                      "scopes": MASTODON_SCOPES})
    app = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or not app.get("client_id") or not app.get("client_secret"):
        raise OAuthError(f"That server did not accept STARK AI ({r.status_code}). Check the address.")
    now = time.time()
    for k in [k for k, v in PENDING.items() if v["created"] < now - TTL_S]:
        PENDING.pop(k, None)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    PENDING[state] = {"platform": "mastodon", "base": base, "client_id": app["client_id"],
                      "client_secret": app["client_secret"], "verifier": verifier, "redirect": redirect, "created": now}
    q = {"client_id": app["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": MASTODON_SCOPES,
         "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}  # PKCE: Mastodon 4.3+
    return f"{base}/oauth/authorize?{urlencode(q)}"


def server_origin(url: str) -> str:
    """Only scheme, host and port: a pasted profile link or a path/query can't redirect the API calls elsewhere."""
    from urllib.parse import urlsplit  # noqa: PLC0415

    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise OAuthError("Enter your Mastodon server, for example mastodon.social.")
    host = p.hostname if ":" not in p.hostname else f"[{p.hostname}]"
    return f"https://{host}{f':{p.port}' if p.port else ''}"


def mastodon_base(state: str) -> str:
    return PENDING.get(state, {}).get("base", "")


def is_mastodon(state: str) -> bool:
    return PENDING.get(state, {}).get("platform") == "mastodon"


async def mastodon_finish(state: str, code: str, transport=None) -> tuple[str, str]:
    """Returns (server, access token)."""
    pending = PENDING.pop(state, None)
    if not pending or pending["platform"] != "mastodon" or pending["created"] < time.time() - TTL_S:
        raise OAuthError("This sign-in link expired or was already used. Start again from Your accounts.")
    body = {"grant_type": "authorization_code", "code": code, "client_id": pending["client_id"],
            "client_secret": pending["client_secret"], "redirect_uri": pending["redirect"],
            "code_verifier": pending["verifier"], "scope": MASTODON_SCOPES}
    async with httpx.AsyncClient(timeout=30, transport=transport, follow_redirects=False) as c:
        r = await c.post(f"{pending['base']}/oauth/token", data=body)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or not data.get("access_token"):
        raise OAuthError(f"Mastodon did not return a token ({data.get('error') or r.status_code}).")
    return pending["base"], data["access_token"]


def open_in_system_browser(url: str) -> bool:
    """Desktop app only: sign-in happens in the user's normal browser (saved passwords, passkeys, 2FA apps),
    never inside the app window. Only https sign-in pages are opened."""
    if not url.startswith("https://"):
        return False
    import webbrowser  # noqa: PLC0415

    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:  # noqa: BLE001 - no browser registered: the page shows the link instead
        return False
