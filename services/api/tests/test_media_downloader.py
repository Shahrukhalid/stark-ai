from __future__ import annotations

import pytest
from stark.media_downloader import (
    MediaDownloaderError,
    safe_filename,
    validate_url,
    ytdlp_available,
)


def test_safe_filename():
    assert safe_filename("simple_video.mp4") == "simple_video.mp4"
    assert safe_filename("path/to/video:special*chars?.mp4") == "video_special_chars_.mp4"
    assert safe_filename("") == "video"


def test_validate_url_ssrf_guards():
    # Valid external URLs
    assert validate_url("https://example.com/video.mp4") == "https://example.com/video.mp4"
    assert validate_url("http://cdn.example.org/clip") == "http://cdn.example.org/clip"

    # Localhost / Loopback rejection
    with pytest.raises(MediaDownloaderError, match="Local network addresses"):
        validate_url("http://localhost:8000/media.mp4")

    with pytest.raises(MediaDownloaderError, match="Private and local"):
        validate_url("http://127.0.0.1:8000/media.mp4")

    # Private network rejection (SSRF guard)
    with pytest.raises(MediaDownloaderError, match="Private and local"):
        validate_url("http://192.168.1.10/video.mp4")

    with pytest.raises(MediaDownloaderError, match="Private and local"):
        validate_url("http://10.0.0.5/video.mp4")

    # Invalid schemes
    with pytest.raises(MediaDownloaderError, match="Only http:// and https://"):
        validate_url("file:///etc/passwd")

    with pytest.raises(MediaDownloaderError, match="Please provide a valid"):
        validate_url("")


def test_ytdlp_availability():
    # yt-dlp is installed in test environment
    assert ytdlp_available() is True
