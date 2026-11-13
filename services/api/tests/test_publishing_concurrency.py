from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
import pytest
from sqlalchemy import select, update
import httpx

from stark.config import Settings
from stark.core.db import Database, ContentItem
from stark.core.runtime import ToolContext
from stark.main import build_kernel
from stark.tools.social import Social, _resolve_lock_dir, _interprocess_platform_lock, _async_interprocess_platform_lock
from stark.tools import social as social_tools


def _ctx(k, agent_id: str = "edith"):
    t = k._create_task(agent_id=agent_id, instruction="test task")
    return ToolContext(kernel=k, agent=k.registry.get(agent_id), task_id=t["id"], root_id=t["id"], settings=k.settings)


async def _draft(k, platform: str = "mastodon", text: str = "Test post text"):
    tool = k.tools.get("social.draft")
    res = await tool.run(tool.Input(platform=platform, text=text), _ctx(k, "edith"))
    assert res.ok, res.summary
    return res.data


def _make_file_db_kernel(db_path: Path, data_dir: Path):
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{db_path}",
        workspace_roots=[data_dir],
    )
    kernel = build_kernel(settings)
    return kernel, settings


@pytest.mark.asyncio
async def test_cross_process_file_locking_mutual_exclusion(tmp_path: Path):
    """Confirms real OS-level mutual exclusion between independent operating system processes."""
    lock_dir = tmp_path / "proc_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    worker_code = f"""
import sys, time
from pathlib import Path
from stark.tools.social import _interprocess_platform_lock

platform = sys.argv[1]
lock_dir = Path(sys.argv[2])
hold_s = float(sys.argv[3])
timeout_s = float(sys.argv[4])

try:
    with _interprocess_platform_lock(platform, lock_dir=lock_dir, timeout_s=timeout_s):
        print("ACQUIRED", flush=True)
        time.sleep(hold_s)
        print("RELEASED", flush=True)
    sys.exit(0)
except TimeoutError:
    print("TIMEOUT", flush=True)
    sys.exit(2)
except Exception as e:
    print(f"ERROR: {{e}}", flush=True)
    sys.exit(1)
"""
    script = tmp_path / "lock_worker.py"
    script.write_text(worker_code, encoding="utf-8")

    api_root = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=api_root)

    # Start Process 1: holds lock for 2.5 seconds (allowing for Windows process startup and module imports)
    p1 = subprocess.Popen(
        [sys.executable, str(script), "x", str(lock_dir), "2.5", "5.0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    # Wait until Process 1 has acquired the lock
    while True:
        line = p1.stdout.readline()
        if "ACQUIRED" in line:
            break
        if p1.poll() is not None:
            pytest.fail(f"Process 1 exited prematurely: {p1.stderr.read()}")

    # Process 2 tries to acquire with a short 0.2s timeout and MUST time out
    p2 = subprocess.run(
        [sys.executable, str(script), "x", str(lock_dir), "0.1", "0.2"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p2.returncode == 2
    assert "TIMEOUT" in p2.stdout

    # Process 1 finishes and releases
    p1_out, p1_err = p1.communicate(timeout=10)
    assert p1.returncode == 0
    assert "RELEASED" in p1_out

    # Process 3 can now acquire immediately
    p3 = subprocess.run(
        [sys.executable, str(script), "x", str(lock_dir), "0.05", "2.0"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p3.returncode == 0
    assert "ACQUIRED" in p3.stdout


@pytest.mark.asyncio
async def test_multi_instance_separate_data_dirs_shared_db(tmp_path: Path, monkeypatch):
    """Verifies concurrency safety when two application instances have separate data directories but share a database."""
    shared_db = tmp_path / "shared_cluster.db"
    dir_a = tmp_path / "instance_a"
    dir_b = tmp_path / "instance_b"
    dir_a.mkdir()
    dir_b.mkdir()

    shared_locks = shared_db.parent / "locks"
    monkeypatch.setenv("STARK_LOCK_DIR", str(shared_locks))

    # Derived lock dir must resolve adjacent to shared db via STARK_LOCK_DIR
    k_a, set_a = _make_file_db_kernel(shared_db, dir_a)
    k_b, set_b = _make_file_db_kernel(shared_db, dir_b)

    lock_dir_a = _resolve_lock_dir(set_a, k_a)
    lock_dir_b = _resolve_lock_dir(set_b, k_b)
    assert lock_dir_a == lock_dir_b, "Separate instances with shared DB must share canonical lock dir"
    assert lock_dir_a == shared_locks

    # Create a draft in shared database
    draft = await _draft(k_a, platform="mastodon", text="Shared cluster concurrent publish test")
    item_id = draft["id"]

    # Both instances simulate publish attempt simultaneously
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"id": "masto-shared-1"}))
    sc_a = Social(set_a, transport)
    sc_b = Social(set_b, transport)

    results = await asyncio.gather(
        social_tools.publish_item(k_a, sc_a, set_a, item_id, approved_fp=None, actor="user"),
        social_tools.publish_item(k_b, sc_b, set_b, item_id, approved_fp=None, actor="user"),
    )

    # Exactly one instance must succeed, the other must fail with not_publishable
    successes = [r for r in results if r.ok]
    failures = [r for r in results if not r.ok]
    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
    assert len(failures) == 1, f"Expected 1 failure, got {len(failures)}"
    assert failures[0].error in ("not_publishable", "lock_failed")


@pytest.mark.asyncio
async def test_shared_db_duplicate_prevention_across_instances(tmp_path: Path):
    """Confirms content hash deduplication blocks publishing identical text across separate application instances."""
    shared_db = tmp_path / "cluster_dedup.db"
    dir_a = tmp_path / "node_a"
    dir_b = tmp_path / "node_b"
    dir_a.mkdir()
    dir_b.mkdir()

    k_a, set_a = _make_file_db_kernel(shared_db, dir_a)
    k_b, set_b = _make_file_db_kernel(shared_db, dir_b)

    # Configure live adapter credentials
    set_a.mastodon_base_url, set_a.mastodon_access_token = "https://social.example", "masto-tok-1"
    set_b.mastodon_base_url, set_b.mastodon_access_token = "https://social.example", "masto-tok-2"

    calls = []
    def handler(req: httpx.Request):
        calls.append(req.url.path)
        return httpx.Response(200, json={"id": f"masto-live-{len(calls)}", "url": f"https://social.example/@user/{len(calls)}"})

    transport = httpx.MockTransport(handler)
    sc_a = Social(set_a, transport)
    sc_b = Social(set_b, transport)

    # Node A drafts and publishes post
    d1 = await _draft(k_a, platform="mastodon", text="Breaking product announcement!")
    p1 = await social_tools.publish_item(k_a, sc_a, set_a, d1["id"], approved_fp=None, actor="user")
    assert p1.ok

    # Node B drafts identical text and tries to publish to the same platform
    d2 = await _draft(k_b, platform="mastodon", text="Breaking product announcement!")
    p2 = await social_tools.publish_item(k_b, sc_b, set_b, d2["id"], approved_fp=None, actor="user")
    assert not p2.ok
    assert p2.error in ("duplicate_post", "publishing_policy_violation")


@pytest.mark.asyncio
async def test_crash_recovery_on_startup(tmp_path: Path):
    """Verifies that an unhandled crash during publishing is safely recovered upon next startup."""
    db_path = tmp_path / "crash_recovery.db"
    data_dir = tmp_path / "crash_data"
    data_dir.mkdir()

    settings = Settings(data_dir=data_dir, database_url=f"sqlite:///{db_path}")

    # First initialize kernel to create all database tables
    init_kernel = build_kernel(settings)

    # Seed 2 items stuck in 'publishing' state before simulated crash
    now = datetime.now(timezone.utc)
    with init_kernel.db.session() as s:
        c1 = ContentItem(
            id="item-crashed-published",
            text="Post was live before crash",
            platform="x",
            status="publishing",
            created_by="user",
            external_id="ext-live-12345",
            created_at=now,
            updated_at=now,
        )
        c2 = ContentItem(
            id="item-crashed-unconfirmed",
            text="Post was in-flight when crashed",
            platform="x",
            status="publishing",
            created_by="user",
            external_id=None,
            created_at=now,
            updated_at=now,
        )
        s.add_all([c1, c2])

    # Now simulate fresh startup / API restart
    recovered_kernel = build_kernel(settings)

    # Inspect recovered database state
    with recovered_kernel.db.session() as s:
        recovered_1 = s.get(ContentItem, "item-crashed-published")
        recovered_2 = s.get(ContentItem, "item-crashed-unconfirmed")

        assert recovered_1.status == "published"
        assert "interrupted by API restart" in (recovered_1.error or "")

        assert recovered_2.status == "failed"
        assert "interrupted by API restart while publishing" in (recovered_2.error or "")
        assert "retrying is safe" in (recovered_2.error or "")
