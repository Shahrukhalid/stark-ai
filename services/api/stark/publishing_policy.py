"""Platform publishing safety policies, pacing limits, and duplicate protection.

Enforces safe posting rates, daily caps, minimum spacing between posts,
SHA-256 content deduplication, and HTTP 429 rate-limit backoff across supported platforms.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlatformPolicy:
    platform: str
    max_posts_per_day: int
    recommended_posts_per_day: int
    min_gap_minutes: int
    rate_limit_backoff_minutes: int
    notes: str = ""


# Official API & recommended safe publishing baselines per platform
POLICIES: dict[str, PlatformPolicy] = {
    "youtube": PlatformPolicy(
        platform="youtube",
        max_posts_per_day=10,
        recommended_posts_per_day=6,
        min_gap_minutes=60,
        rate_limit_backoff_minutes=1440,
        notes="Standard YouTube quota; daily quota reset occurs at midnight Pacific Time.",
    ),
    "tiktok": PlatformPolicy(
        platform="tiktok",
        max_posts_per_day=8,
        recommended_posts_per_day=4,
        min_gap_minutes=60,
        rate_limit_backoff_minutes=120,
        notes="High-frequency uploads risk distribution throttling.",
    ),
    "instagram": PlatformPolicy(
        platform="instagram",
        max_posts_per_day=25,
        recommended_posts_per_day=5,
        min_gap_minutes=30,
        rate_limit_backoff_minutes=60,
        notes="Meta Graph API allows up to 25 API-published Reels per 24 hours per account.",
    ),
    "facebook": PlatformPolicy(
        platform="facebook",
        max_posts_per_day=15,
        recommended_posts_per_day=5,
        min_gap_minutes=30,
        rate_limit_backoff_minutes=60,
        notes="Standard Page publishing limits.",
    ),
    "snapchat": PlatformPolicy(
        platform="snapchat",
        max_posts_per_day=5,
        recommended_posts_per_day=3,
        min_gap_minutes=60,
        rate_limit_backoff_minutes=120,
        notes="Spotlight and Story guidelines recommend curated spacing.",
    ),
    "x": PlatformPolicy(
        platform="x",
        max_posts_per_day=50,
        recommended_posts_per_day=12,
        min_gap_minutes=15,
        rate_limit_backoff_minutes=60,
        notes="Standard user tier rate limits.",
    ),
    "linkedin": PlatformPolicy(
        platform="linkedin",
        max_posts_per_day=25,
        recommended_posts_per_day=5,
        min_gap_minutes=30,
        rate_limit_backoff_minutes=60,
        notes="Professional feed distribution guidelines.",
    ),
    "bluesky": PlatformPolicy(
        platform="bluesky",
        max_posts_per_day=50,
        recommended_posts_per_day=10,
        min_gap_minutes=10,
        rate_limit_backoff_minutes=30,
        notes="AT Protocol guidelines.",
    ),
    "threads": PlatformPolicy(
        platform="threads",
        max_posts_per_day=25,
        recommended_posts_per_day=5,
        min_gap_minutes=30,
        rate_limit_backoff_minutes=60,
        notes="Threads API publishing limits.",
    ),
}

DEFAULT_POLICY = PlatformPolicy(
    platform="generic",
    max_posts_per_day=10,
    recommended_posts_per_day=5,
    min_gap_minutes=30,
    rate_limit_backoff_minutes=60,
    notes="Default fallback policy.",
)

# In-memory rate-limit cooldown records: platform -> timestamp until which posting is paused
_COOLDOWNS: dict[str, float] = {}


def sync_cooldowns_from_db(db: Any) -> None:
    """Loads persistent rate-limit cooldowns from the settings store."""
    if not db:
        return
    try:
        saved = db.get_setting("publishing_cooldowns", {})
        if isinstance(saved, dict):
            now = time.time()
            for k, v in saved.items():
                if isinstance(v, (int, float)) and v > now:
                    _COOLDOWNS[k.lower()] = float(v)
    except Exception:
        pass


def get_policy(platform: str) -> PlatformPolicy:
    return POLICIES.get(platform.lower(), DEFAULT_POLICY)


def record_rate_limit(platform: str, backoff_minutes: int | None = None, db: Any = None) -> float:
    """Record an HTTP 429 / rate-limit response and set cooldown, persisting to DB if available."""
    policy = get_policy(platform)
    mins = backoff_minutes if backoff_minutes is not None else policy.rate_limit_backoff_minutes
    until = time.time() + mins * 60
    _COOLDOWNS[platform.lower()] = until
    if db:
        try:
            current = db.get_setting("publishing_cooldowns", {})
            if not isinstance(current, dict):
                current = {}
            current[platform.lower()] = until
            db.set_setting("publishing_cooldowns", current)
        except Exception:
            pass
    return until


def clear_cooldown(platform: str, db: Any = None) -> None:
    _COOLDOWNS.pop(platform.lower(), None)
    if db:
        try:
            current = db.get_setting("publishing_cooldowns", {})
            if isinstance(current, dict) and platform.lower() in current:
                current.pop(platform.lower(), None)
                db.set_setting("publishing_cooldowns", current)
        except Exception:
            pass


def get_cooldown_remaining(platform: str) -> float:
    until = _COOLDOWNS.get(platform.lower(), 0.0)
    remaining = until - time.time()
    return max(0.0, remaining)


def content_hash(text: str, media_bytes: bytes | None = None) -> str:
    """SHA-256 fingerprint for deduplication."""
    h = hashlib.sha256(text.strip().encode("utf-8"))
    if media_bytes:
        h.update(media_bytes[:65536])
    return h.hexdigest()


def check_eligibility(
    platform: str,
    recent_post_timestamps: list[float] | None = None,
    proposed_timestamp: float | None = None,
) -> dict[str, Any]:
    """Evaluates whether publishing to this platform is safe right now."""
    policy = get_policy(platform)
    now = proposed_timestamp or time.time()

    # 1. Active 429 Cooldown check
    remaining = get_cooldown_remaining(platform)
    if remaining > 0:
        return {
            "eligible": False,
            "reason": f"Platform is in rate-limit cooldown for {round(remaining / 60, 1)} more minutes.",
            "policy": policy,
            "wait_seconds": round(remaining, 1),
        }

    # 2. Daily Quota check (last 24 hours)
    timestamps = [t for t in (recent_post_timestamps or []) if now - t < 86400]
    if len(timestamps) >= policy.max_posts_per_day:
        return {
            "eligible": False,
            "reason": f"Daily post limit reached ({len(timestamps)}/{policy.max_posts_per_day} in 24h).",
            "policy": policy,
            "wait_seconds": 86400 - (now - min(timestamps)),
        }

    # 3. Minimum gap check
    if timestamps:
        last_post = max(timestamps)
        elapsed_min = (now - last_post) / 60
        if elapsed_min < policy.min_gap_minutes:
            wait_s = (policy.min_gap_minutes * 60) - (now - last_post)
            return {
                "eligible": False,
                "reason": f"Pacing interval: please wait {round(wait_s / 60, 1)} minutes before next post.",
                "policy": policy,
                "wait_seconds": round(wait_s, 1),
            }

    return {
        "eligible": True,
        "reason": "OK",
        "policy": policy,
        "posts_24h": len(timestamps),
        "wait_seconds": 0.0,
    }
