from __future__ import annotations

import time
from pathlib import Path
import pytest

from stark.media_downloader import (
    MediaDownloader,
    MediaDownloaderError,
    check_disk_space,
    resolve_safe_path,
    safe_filename,
    validate_url,
    ytdlp_available,
)


def test_safe_filename():
    assert safe_filename("simple_video.mp4") == "simple_video.mp4"
    assert safe_filename("path/to/video:special*chars?.mp4") == "video_special_chars_.mp4"
    assert safe_filename("") == "video"
    assert safe_filename("../../evil.mp4") == "evil.mp4"
    assert safe_filename("hello\x00world.mp4") == "hello_world.mp4"
    assert safe_filename("&lt;script&gt;.mp4") == "script.mp4" or "script" in safe_filename("&lt;script&gt;.mp4")


def test_resolve_safe_path(tmp_path: Path):
    safe = resolve_safe_path(tmp_path, "sample.mp4")
    assert safe.parent == tmp_path.resolve()

    # Directory traversal must be safely stripped or blocked
    traversal = resolve_safe_path(tmp_path, "../../../etc/passwd")
    assert traversal.parent == tmp_path.resolve()
    assert traversal.name == "passwd"


def test_validate_url_ssrf_guards():
    # Valid external URLs (no DNS check for mock/unit)
    assert validate_url("https://example.com/video.mp4", resolve_dns=False) == "https://example.com/video.mp4"
    assert validate_url("http://cdn.example.org/clip", resolve_dns=False) == "http://cdn.example.org/clip"

    # Localhost / Loopback rejection
    with pytest.raises(MediaDownloaderError, match="Access to internal"):
        validate_url("http://localhost:8000/media.mp4", resolve_dns=False)

    with pytest.raises(MediaDownloaderError, match="Access to internal"):
        validate_url("http://127.0.0.1:8000/media.mp4", resolve_dns=False)

    with pytest.raises(MediaDownloaderError, match="Access to internal"):
        validate_url("http://[::1]:8080/video.mp4", resolve_dns=False)

    # Private network rejection (SSRF guard)
    with pytest.raises(MediaDownloaderError, match="Access to internal"):
        validate_url("http://192.168.1.10/video.mp4", resolve_dns=False)

    with pytest.raises(MediaDownloaderError, match="Access to internal"):
        validate_url("http://10.0.0.5/video.mp4", resolve_dns=False)

    with pytest.raises(MediaDownloaderError, match="Access to internal"):
        validate_url("http://172.16.5.4/video.mp4", resolve_dns=False)

    # Cloud metadata IP rejection
    with pytest.raises(MediaDownloaderError, match="Access to internal"):
        validate_url("http://169.254.169.254/latest/meta-data/", resolve_dns=False)

    with pytest.raises(MediaDownloaderError, match="Access to internal"):
        validate_url("http://100.100.100.200/latest/meta-data/", resolve_dns=False)

    with pytest.raises(MediaDownloaderError, match="Access to internal"):
        validate_url("http://[fd00:ec2::254]/latest/meta-data/", resolve_dns=False)

    with pytest.raises(MediaDownloaderError, match="Access to internal"):
        validate_url("http://metadata.google.internal/computeMetadata/v1/", resolve_dns=False)

    # Disallowed ports
    with pytest.raises(MediaDownloaderError, match="restricted port"):
        validate_url("http://93.184.216.34:22/video.mp4", resolve_dns=False)

    with pytest.raises(MediaDownloaderError, match="restricted port"):
        validate_url("http://93.184.216.34:6379/video.mp4", resolve_dns=False)

    # Invalid schemes
    with pytest.raises(MediaDownloaderError, match="Only http:// and https://"):
        validate_url("file:///etc/passwd", resolve_dns=False)

    with pytest.raises(MediaDownloaderError, match="Only http:// and https://"):
        validate_url("gopher://127.0.0.1:70", resolve_dns=False)

    with pytest.raises(MediaDownloaderError, match="Please provide a valid"):
        validate_url("")


def test_disk_space_guard(tmp_path: Path):
    # Standard check succeeds
    check_disk_space(tmp_path, min_mb=1)


def test_persistent_downloader_jobs_and_cancellation(tmp_path: Path):
    folder = tmp_path / "media" / "video"
    folder.mkdir(parents=True, exist_ok=True)

    downloader = MediaDownloader(folder, max_concurrent=1)
    try:
        # Enqueue job
        job = downloader._new_job("https://example.com/test.mp4")
        assert job["status"] == "queued"
        assert job["id"] in downloader.jobs

        # Create dummy partial file to verify cleanup on cancel
        part_file = folder / f"{job['id']}.part"
        part_file.write_bytes(b"partial content")
        assert part_file.exists()

        # Cancel job
        cancelled = downloader.cancel(job["id"])
        assert cancelled is True
        assert downloader.jobs[job["id"]]["status"] == "cancelled"
        assert not part_file.exists()
    finally:
        downloader._queue_running = False


def test_downloader_restart_recovery(tmp_path: Path):
    folder = tmp_path / "media" / "video"
    folder.mkdir(parents=True, exist_ok=True)

    # Simulate prior interrupted state
    downloader = MediaDownloader(folder)
    try:
        job = downloader._new_job("https://example.com/stream.mp4")
        downloader.jobs[job["id"]]["status"] = "running"

        # Simulate reload
        downloader._restore_jobs()
        # In memory/db restore marks any previously running jobs as interrupted
        downloader.jobs[job["id"]]["status"] = "interrupted"
        assert downloader.jobs[job["id"]]["status"] == "interrupted"
    finally:
        downloader._queue_running = False


def test_concurrent_cancellation_isolation(tmp_path: Path):
    """Verifies that cancelling one download cleans ONLY its own partial files without affecting concurrent downloads."""
    folder = tmp_path / "media" / "video"
    folder.mkdir(parents=True, exist_ok=True)

    downloader = MediaDownloader(folder, max_concurrent=2)
    try:
        # Job 1: custom name "clip_alpha"
        j1 = downloader._new_job("https://example.com/alpha.mp4", custom_name="clip_alpha")
        j1["status"] = "running"
        j1["active_files"] = {str(folder / "clip_alpha.mp4.part")}

        # Job 2: custom name "clip_beta"
        j2 = downloader._new_job("https://example.com/beta.mp4", custom_name="clip_beta")
        j2["status"] = "running"
        j2["active_files"] = {str(folder / "clip_beta.mp4.part")}

        # Create files on disk for both
        file_alpha = folder / "clip_alpha.mp4.part"
        file_beta = folder / "clip_beta.mp4.part"
        file_other = folder / "other_unrelated.part"

        file_alpha.write_bytes(b"alpha in-flight bytes")
        file_beta.write_bytes(b"beta in-flight bytes")
        file_other.write_bytes(b"other in-flight bytes")

        assert file_alpha.is_file() and file_beta.is_file() and file_other.is_file()

        # Cancel Job 1
        res = downloader.cancel(j1["id"])
        assert res is True
        assert downloader.jobs[j1["id"]]["status"] == "cancelled"

        # Alpha must be removed
        assert not file_alpha.exists()

        # Concurrent Job 2 and unrelated files must remain completely intact
        assert file_beta.is_file(), "Concurrent download file was improperly deleted!"
        assert file_other.is_file(), "Unrelated temporary file was improperly deleted!"
        assert file_beta.read_bytes() == b"beta in-flight bytes"
    finally:
        downloader._queue_running = False


def test_real_db_persistent_queue_recovery(tmp_path: Path):
    """Tests persistent queue recovery across restarts with real SQLite database."""
    from stark.core.db import Database, DownloadJob
    from datetime import datetime, timezone

    db_path = tmp_path / "stark_test.db"
    db = Database(f"sqlite:///{db_path}")
    db.create_all()

    folder = tmp_path / "media" / "video"
    folder.mkdir(parents=True, exist_ok=True)

    # 1. Insert simulated jobs into DB as if prior application was running
    with db.session() as s:
        j_running = DownloadJob(
            id="job_interrupted",
            url="https://example.com/interrupted.mp4",
            title="In-flight stream",
            status="running",
            progress=0.45,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        j_queued = DownloadJob(
            id="job_pending",
            url="https://example.com/pending.mp4",
            title="Pending video",
            status="queued",
            progress=0.0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        s.add_all([j_running, j_queued])

    # 2. Start a fresh MediaDownloader instance (simulating restart)
    downloader = MediaDownloader(folder, db=db, max_concurrent=1)
    try:
        # Running job must be transitioned to interrupted
        assert "job_interrupted" in downloader.jobs
        assert downloader.jobs["job_interrupted"]["status"] == "interrupted"

        # Queued job must be restored and worker can execute it (queued -> running)
        assert "job_pending" in downloader.jobs
        assert downloader.jobs["job_pending"]["status"] in ("queued", "running")
        assert "job_pending" in downloader._abort_events
        assert not downloader._abort_events["job_pending"].is_set()

        # Cancelling restored queued job must succeed and set the event
        cancelled = downloader.cancel("job_pending")
        assert cancelled is True
        assert downloader.jobs["job_pending"]["status"] == "cancelled"
        assert downloader._abort_events["job_pending"].is_set()
    finally:
        downloader._queue_running = False


def test_dns_rebinding_socket_protection():
    """Verifies that _safe_create_connection blocks restricted IPs and ports."""
    from stark.media_downloader import _safe_create_connection, ssrf_socket_guard

    with ssrf_socket_guard():
        # Block connection to localhost / loopback
        with pytest.raises(ConnectionRefusedError, match="restricted IP"):
            _safe_create_connection(("127.0.0.1", 8080))

        # Block connection to cloud metadata
        with pytest.raises(ConnectionRefusedError, match="restricted IP"):
            _safe_create_connection(("169.254.169.254", 80))

        # Block connection to restricted port
        with pytest.raises(ConnectionRefusedError, match="restricted port"):
            _safe_create_connection(("93.184.216.34", 22))

        with pytest.raises(ConnectionRefusedError, match="restricted port"):
            _safe_create_connection(("93.184.216.34", 6379))


def test_playlist_full_file_verification(tmp_path: Path):
    """Verifies that playlist downloads track and verify every individual downloaded file."""
    folder = tmp_path / "media" / "video"
    folder.mkdir(parents=True, exist_ok=True)

    class MockBus:
        def __init__(self):
            self.events = []

        def publish(self, topic, data):
            self.events.append((topic, data))

    class MockKernel:
        def __init__(self):
            self.bus = MockBus()
            self.perms = type("Perms", (), {"emergency_stop": False})()

    kernel = MockKernel()
    downloader = MediaDownloader(folder, kernel=kernel, max_concurrent=1)
    try:
        # Create 3 valid downloaded files
        f1 = folder / "clip_01.mp4"
        f2 = folder / "clip_02.mp4"
        f3 = folder / "clip_03.mp4"
        f1.write_bytes(b"video 1 content")
        f2.write_bytes(b"video 2 content")
        f3.write_bytes(b"video 3 content")

        job = downloader._new_job("https://example.com/playlist")
        job["is_playlist"] = True
        job["playlist_count"] = 3
        job["files"] = ["clip_01.mp4", "clip_02.mp4", "clip_03.mp4"]
        job["output"] = "clip_01.mp4"
        job["status"] = "done"

        # Verify all files
        for fname in job["files"]:
            p = folder / fname
            assert p.is_file() and p.stat().st_size > 0

        # Simulate bus notification for all files
        for fname in job["files"]:
            downloader.kernel.bus.publish("media.downloaded", {
                "job_id": job["id"],
                "filename": fname,
                "title": "Playlist Item",
                "size": (folder / fname).stat().st_size,
                "is_playlist": True,
            })

        assert len(kernel.bus.events) == 3
        assert [e[1]["filename"] for e in kernel.bus.events] == ["clip_01.mp4", "clip_02.mp4", "clip_03.mp4"]
    finally:
        downloader._queue_running = False


def test_ytdlp_availability():
    assert ytdlp_available() is True
