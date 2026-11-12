"""Media tools: media acquisition, video processing, thumbnails, and publishing health.

Registered tools for agents (U, Vision, Wanda, J.A.R.V.I.S., D.U.M.-E.) to interact with the
multimedia engine, video studio, and platform publishing policies safely.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Literal
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core.db import ContentItem, utcnow
from ..core.tool_registry import Risk, Tool, ToolResult
from .. import publishing_policy


class MediaDownloadInput(BaseModel):
    url: str = Field(..., description="Public http(s) URL of the video or playlist to download.")
    resolution: str | None = Field(None, description="Preferred resolution (e.g. '1080p', '720p').")
    audio_only: bool = Field(False, description="Extract audio as MP3 only.")
    custom_name: str | None = Field(None, description="Custom safe filename stem without extension.")


class MediaDownloadTool(Tool):
    id = "media.download"
    category = "media"
    description = "Download public media, video or audio from the web into the workspace media/video folder."
    Input = MediaDownloadInput
    risk = Risk.EXTERNAL
    timeout_s = 60

    async def run(self, args: MediaDownloadInput, ctx) -> ToolResult:
        if not hasattr(ctx.kernel, "media_downloader"):
            return ToolResult(False, "Media downloader engine not initialized on kernel.", error="engine_unavailable")
        try:
            job = ctx.kernel.media_downloader.start_download(
                url=args.url,
                resolution=args.resolution,
                audio_only=args.audio_only,
                custom_name=args.custom_name,
            )
            ctx.kernel.obs.audit(ctx.actor or "agent", "media.download", "ok", target=args.url)
            return ToolResult(True, f"Download started: job ID {job['id']}", data=job)
        except Exception as exc:
            return ToolResult(False, f"Download failed to start: {exc}", error=str(exc))


class MediaAnalyzeInput(BaseModel):
    url: str = Field(..., description="Public http(s) URL of the media source to inspect.")


class MediaAnalyzeTool(Tool):
    id = "media.analyze"
    category = "media"
    description = "Inspect remote video or playlist metadata (title, duration, author, format) without downloading."
    Input = MediaAnalyzeInput
    risk = Risk.READ
    timeout_s = 30

    async def run(self, args: MediaAnalyzeInput, ctx) -> ToolResult:
        if not hasattr(ctx.kernel, "media_downloader"):
            return ToolResult(False, "Media downloader engine not initialized on kernel.", error="engine_unavailable")
        try:
            info = await asyncio.to_thread(ctx.kernel.media_downloader.analyze, args.url)
            return ToolResult(True, f"Extracted metadata for: {info.get('title', 'Media')}", data=info)
        except Exception as exc:
            return ToolResult(False, f"Media analysis failed: {exc}", error=str(exc))


class VideoProcessInput(BaseModel):
    file: str = Field(..., description="Name of the input video file in the media/video directory.")
    preset: Literal["vertical", "square", "portrait", "landscape", "original"] = Field("vertical", description="Target aspect ratio preset.")
    fit: Literal["pad", "crop"] = Field("pad", description="Framing mode: pad (letterbox/pillarbox) or crop (fill).")
    start: float | None = Field(None, ge=0, description="Start timestamp in seconds.")
    end: float | None = Field(None, ge=0, description="End timestamp in seconds.")
    normalize_audio: bool = Field(False, description="Apply broadcast -14 LUFS loudness normalization.")
    brightness: float = Field(0.0, ge=-1.0, le=1.0, description="Brightness adjustment (-0.5 to 0.5).")
    contrast: float = Field(1.0, ge=-2.0, le=2.0, description="Contrast multiplier (0.5 to 1.5).")
    saturation: float = Field(1.0, ge=0.0, le=3.0, description="Color saturation multiplier (0 to 2.0).")
    gamma: float = Field(1.0, ge=0.1, le=10.0, description="Gamma correction factor.")
    watermark_text: str | None = Field(None, description="Text branding to overlay.")
    watermark_position: Literal["top-left", "top-right", "bottom-left", "bottom-right", "center"] = Field("bottom-right", description="Position of watermark overlay.")


class VideoProcessTool(Tool):
    id = "video.process"
    category = "media"
    description = "Process, trim, reframe, grade, normalize audio, or watermark a video in the workspace media studio."
    Input = VideoProcessInput
    risk = Risk.WRITE
    timeout_s = 300

    async def run(self, args: VideoProcessInput, ctx) -> ToolResult:
        if not hasattr(ctx.kernel, "video"):
            return ToolResult(False, "Video studio engine not available on kernel.", error="engine_unavailable")
        try:
            opts = {
                "file": args.file,
                "preset": args.preset,
                "fit": args.fit,
                "start": args.start,
                "end": args.end,
                "normalize_audio": args.normalize_audio,
                "brightness": args.brightness,
                "contrast": args.contrast,
                "saturation": args.saturation,
                "gamma": args.gamma,
                "watermark_text": args.watermark_text,
                "watermark_position": args.watermark_position,
                "format": "mp4",
            }
            job = ctx.kernel.video.start(opts)
            ctx.kernel.obs.audit(ctx.actor or "agent", "video.process", "ok", target=args.file)
            return ToolResult(True, f"Video export started: job {job['id']}", data=job)
        except Exception as exc:
            return ToolResult(False, f"Video processing failed: {exc}", error=str(exc))


class VideoThumbnailInput(BaseModel):
    file: str = Field(..., description="Name of the video file in media/video directory.")
    at: float = Field(1.0, ge=0, description="Timestamp in seconds to extract cover frame.")


class VideoThumbnailTool(Tool):
    id = "video.thumbnail"
    category = "media"
    description = "Generate a high-quality thumbnail image frame from a video in the media library."
    Input = VideoThumbnailInput
    risk = Risk.WRITE
    timeout_s = 30

    async def run(self, args: VideoThumbnailInput, ctx) -> ToolResult:
        if not hasattr(ctx.kernel, "video"):
            return ToolResult(False, "Video studio engine not available.", error="engine_unavailable")
        try:
            thumb = await ctx.kernel.video.thumbnail(args.file, args.at)
            return ToolResult(True, f"Thumbnail generated: {thumb['file']}", data=thumb)
        except Exception as exc:
            return ToolResult(False, f"Thumbnail extraction failed: {exc}", error=str(exc))


class PublishingHealthInput(BaseModel):
    platform: str = Field("all", description="Platform name ('youtube', 'tiktok', 'instagram', 'x', etc.) or 'all'.")


class PublishingHealthTool(Tool):
    id = "publishing.health"
    category = "social"
    description = "Check platform publishing limits, daily quota consumption, active rate limits, and safety status."
    Input = PublishingHealthInput
    risk = Risk.READ
    timeout_s = 15

    async def run(self, args: PublishingHealthInput, ctx) -> ToolResult:
        publishing_policy.sync_cooldowns_from_db(ctx.kernel.db)
        platforms_to_check = (
            list(publishing_policy.POLICIES.keys())
            if args.platform.lower() == "all"
            else [args.platform.lower()]
        )
        report: dict[str, Any] = {}
        since_24h = utcnow() - timedelta(days=1)

        with ctx.kernel.db.session() as s:
            for plat in platforms_to_check:
                policy = publishing_policy.get_policy(plat)
                cooldown_left = publishing_policy.get_cooldown_remaining(plat)
                rows = s.scalars(select(ContentItem.published_at).where(
                    ContentItem.platform == plat,
                    ContentItem.status == "published",
                    ContentItem.published_at >= since_24h,
                )).all()
                recent_ts = [r.timestamp() for r in rows if r is not None]
                elig = publishing_policy.check_eligibility(plat, recent_ts)

                report[plat] = {
                    "max_posts_per_day": policy.max_posts_per_day,
                    "recommended_posts_per_day": policy.recommended_posts_per_day,
                    "min_gap_minutes": policy.min_gap_minutes,
                    "posts_published_last_24h": len(recent_ts),
                    "cooldown_remaining_seconds": round(cooldown_left, 1),
                    "eligible_now": elig["eligible"],
                    "status_reason": elig["reason"],
                }

        return ToolResult(True, f"Publishing health report for {len(report)} platform(s)", data=report)


TOOLS = [
    MediaDownloadTool(),
    MediaAnalyzeTool(),
    VideoProcessTool(),
    VideoThumbnailTool(),
    PublishingHealthTool(),
]


def build(settings: Any) -> list[Tool]:
    return TOOLS
