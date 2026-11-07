"""Assistant routines: quiet hours and the spoken daily summary.

* Quiet hours (e.g. 22:00-07:00): no pop-up notifications, reminders wait until the quiet time ends, and the daily
  summary is not spoken.
* Daily summary: at a set time Vision (or Wanda) says what is scheduled today, what went out yesterday, what waits
  for approval and which accounts need attention. Built from STARK AI's own data, so it costs no AI requests.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select

QUIET = "quiet_hours"
SUMMARY = "daily_summary"


def _hm(value: str) -> time | None:
    try:
        h, m = map(int, str(value).split(":"))
        return time(h, m)
    except (ValueError, TypeError):
        return None


def quiet_now(kernel, now: datetime | None = None) -> bool:
    q = kernel.db.get_setting(QUIET) or {}
    start, end = _hm(q.get("start", "")), _hm(q.get("end", ""))
    if not q.get("enabled") or start is None or end is None or start == end:
        return False
    t = (now or datetime.now().astimezone()).time()
    return start <= t < end if start < end else (t >= start or t < end)


def summary_text(kernel, now: datetime | None = None, urdu: bool = False) -> str:
    from .core.db import Approval, ContentItem

    now = now or datetime.now().astimezone()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with kernel.db.session() as s:
        today = s.scalar(select(func.count()).select_from(ContentItem).where(
            ContentItem.status == "scheduled", ContentItem.scheduled_for >= day0.astimezone(timezone.utc),
            ContentItem.scheduled_for < (day0 + timedelta(days=1)).astimezone(timezone.utc))) or 0
        yesterday = s.scalar(select(func.count()).select_from(ContentItem).where(
            ContentItem.status == "published", ContentItem.published_at >= (day0 - timedelta(days=1)).astimezone(timezone.utc),
            ContentItem.published_at < day0.astimezone(timezone.utc))) or 0
        failed = s.scalar(select(func.count()).select_from(ContentItem).where(
            ContentItem.status == "failed", ContentItem.created_at >= (day0 - timedelta(days=1)).astimezone(timezone.utc))) or 0
        waiting = s.scalar(select(func.count()).select_from(Approval).where(Approval.status == "pending")) or 0
    health = kernel.db.get_setting("account_health") or {}
    bad = [a["platform"] for a in health.get("accounts", []) if a.get("status") in ("bad", "warn")]
    reminders = [r for r in (kernel.db.get_setting("reminders") or [])
                 if datetime.fromisoformat(r["at"]).astimezone(now.tzinfo).date() == now.date()]
    if urdu:
        parts = [f"صبح بخیر۔ آج {today} پوسٹس شیڈول ہیں، کل {yesterday} پوسٹس شائع ہوئیں۔"]
        if failed:
            parts.append(f"{failed} پوسٹس ناکام ہوئیں۔")
        if waiting:
            parts.append(f"{waiting} کام آپ کی منظوری کے منتظر ہیں۔")
        if bad:
            parts.append("ان اکاؤنٹس کو دیکھ لیں: " + "، ".join(bad) + "۔")
        if reminders:
            parts.append(f"آج {len(reminders)} یاد دہانیاں ہیں۔")
        return " ".join(parts)
    parts = [f"Good morning. {today} post{'s' if today != 1 else ''} scheduled today; "
             f"{yesterday} went out yesterday."]
    if failed:
        parts.append(f"{failed} failed and need a look.")
    if waiting:
        parts.append(f"{waiting} item{'s' if waiting != 1 else ''} waiting for your approval.")
    if bad:
        parts.append("Check these accounts: " + ", ".join(bad) + ".")
    if reminders:
        parts.append(f"You have {len(reminders)} reminder{'s' if len(reminders) != 1 else ''} today.")
    if len(parts) == 1:
        parts.append("Nothing needs you right now.")
    return " ".join(parts)


def summary_due(kernel, now: datetime) -> bool:
    cfg = kernel.db.get_setting(SUMMARY) or {}
    at = _hm(cfg.get("time", ""))
    if not cfg.get("enabled") or at is None or now.time() < at:
        return False
    return cfg.get("last") != now.date().isoformat()


async def summary_loop(kernel, say: Callable[[str, str], Awaitable[Any]] | None,
                       notify: Callable[[str, str], Any] | None = None) -> None:
    while True:
        try:
            now = datetime.now().astimezone()
            if summary_due(kernel, now):
                cfg = dict(kernel.db.get_setting(SUMMARY) or {})
                cfg["last"] = now.date().isoformat()
                kernel.db.set_setting(SUMMARY, cfg)
                agent = cfg.get("agent") if cfg.get("agent") in ("vision", "wanda") else "vision"
                text = summary_text(kernel, now, urdu=cfg.get("language") == "ur")
                kernel.bus.publish("assistant.summary", {"agent_id": agent, "text": text})
                if not quiet_now(kernel, now):
                    if notify:
                        await asyncio.to_thread(notify, agent.upper(), text)
                    if say:
                        await say(agent, text)
        except Exception:  # noqa: BLE001 - never stop the loop
            pass
        await asyncio.sleep(30)
