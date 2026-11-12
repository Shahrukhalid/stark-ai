from __future__ import annotations

import pytest
from stark.config import Settings
from stark.tools import build_registry
from stark.tools.media_tools import (
    MediaDownloadTool,
    MediaAnalyzeTool,
    VideoProcessTool,
    VideoThumbnailTool,
    PublishingHealthTool,
)


def test_media_tools_registered_in_catalogue():
    settings = Settings()
    reg = build_registry(settings)

    assert reg.get("media.download") is not None
    assert reg.get("media.analyze") is not None
    assert reg.get("video.process") is not None
    assert reg.get("video.thumbnail") is not None
    assert reg.get("publishing.health") is not None


@pytest.mark.asyncio
async def test_publishing_health_tool(settings):
    from .conftest import make_kernel
    kernel, _ = make_kernel(settings)

    tool = PublishingHealthTool()
    ctx = type("Ctx", (), {"kernel": kernel, "actor": "vision"})()

    args = tool.Input(platform="youtube")
    result = await tool.run(args, ctx)

    assert result.ok is True
    assert "youtube" in result.data
    assert result.data["youtube"]["max_posts_per_day"] == 10
    assert result.data["youtube"]["eligible_now"] is True


@pytest.mark.asyncio
async def test_media_download_tool_dispatch(settings, tmp_path):
    from .conftest import make_kernel
    from stark import video, media_downloader

    kernel, _ = make_kernel(settings)
    kernel.video = video.Studio(kernel.settings)
    kernel.media_downloader = media_downloader.MediaDownloader(
        tmp_path, db=kernel.db, kernel=kernel
    )

    tool = MediaDownloadTool()
    ctx = type("Ctx", (), {"kernel": kernel, "actor": "u"})()

    args = tool.Input(url="https://example.com/test.mp4")
    result = await tool.run(args, ctx)

    assert result.ok is True
    assert "job ID" in result.summary
    assert result.data["status"] == "queued"
    kernel.media_downloader._queue_running = False
