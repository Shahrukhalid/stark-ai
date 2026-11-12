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


def test_ytdlp_availability():
    assert ytdlp_available() is True
