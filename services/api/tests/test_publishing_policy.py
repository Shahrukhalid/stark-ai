from __future__ import annotations

import time
from stark.publishing_policy import (
    check_eligibility,
    clear_cooldown,
    content_hash,
    get_cooldown_remaining,
    get_policy,
    record_rate_limit,
)


def test_platform_policy_lookup():
    p = get_policy("youtube")
    assert p.max_posts_per_day == 10
    assert p.min_gap_minutes == 60

    generic = get_policy("unknown_custom_network")
    assert generic.platform == "generic"


def test_rate_limit_cooldown():
    clear_cooldown("tiktok")
    assert get_cooldown_remaining("tiktok") == 0.0

    record_rate_limit("tiktok", backoff_minutes=5)
    remaining = get_cooldown_remaining("tiktok")
    assert remaining > 200

    check = check_eligibility("tiktok")
    assert not check["eligible"]
    assert "rate-limit cooldown" in check["reason"]

    clear_cooldown("tiktok")
    assert get_cooldown_remaining("tiktok") == 0.0


def test_daily_limit_and_gap_pacing():
    clear_cooldown("instagram")
    now = time.time()

    # 1. Fresh account: eligible
    check = check_eligibility("instagram", recent_post_timestamps=[], proposed_timestamp=now)
    assert check["eligible"]

    # 2. Minimum gap check (last post was 5 minutes ago, required gap is 30 mins)
    recent = [now - 300]
    check = check_eligibility("instagram", recent_post_timestamps=recent, proposed_timestamp=now)
    assert not check["eligible"]
    assert "Pacing interval" in check["reason"]

    # 3. 25 posts within 24 hours: daily cap reached
    many = [now - i * 1800 for i in range(1, 26)]
    check = check_eligibility("instagram", recent_post_timestamps=many, proposed_timestamp=now)
    assert not check["eligible"]
    assert "Daily post limit reached" in check["reason"]


def test_content_deduplication_fingerprint():
    h1 = content_hash("Hello world")
    h2 = content_hash("Hello world ")
    h3 = content_hash("Different post")

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64
