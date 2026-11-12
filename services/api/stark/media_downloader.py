"""Media acquisition and web video downloader engine for STARK AI.

Supports safe downloading of public video content, playlists, and profiles using
bundled/installed yt-dlp and FFmpeg, directly into the workspace's media/video folder.
Includes SSRF guards, filename sanitization, background progress, and cancellation.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

logger = logging.getLogger("stark.media_downloader")

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

_INVALID_FS_CHARS = '<>:"/\\|?*'


class MediaDownloaderError(Exception):
    pass


class DownloadCancelledError(Exception):
    pass


def safe_filename(name: str) -> str:
    """Strip invalid characters and limit length for cross-platform filesystem safety."""
    base = os.path.basename(str(name or "").replace("\\", "/"))
    clean = "".join("_" if (c in _INVALID_FS_CHARS or ord(c) < 32) else c for c in base).strip(" .")
    return clean[:120] or "video"


def validate_url(url: str) -> str:
    """Validates public HTTP(S) source URL and guards against SSRF / local address probing."""
    clean = (url or "").strip()
    if not clean or any(ch.isspace() for ch in clean):
        raise MediaDownloaderError("Please provide a valid http(s) URL.")
    if "://" not in clean:
        clean = "https://" + clean
    parsed = urlparse(clean)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise MediaDownloaderError("Only http:// and https:// URLs are supported.")
    host = parsed.hostname.lower()
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".local"):
        raise MediaDownloaderError("Local network addresses cannot be used as media sources.")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise MediaDownloaderError("Private and local network addresses are forbidden.")
    except ValueError:
        pass
    return clean


def ytdlp_available() -> bool:
    return yt_dlp is not None or shutil.which("yt-dlp") is not None


class MediaDownloader:
    """Manages background download tasks with progress tracking and cancellation."""

    def __init__(self, target_folder: Path, ffmpeg_exe: str | None = None) -> None:
        self.folder = target_folder
        self.ffmpeg_exe = ffmpeg_exe
        self.jobs: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs = sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True)[:30]
        return [{k: v for k, v in j.items() if k != "abort"} for j in jobs]

    def _new_job(self, url: str, **kw: Any) -> dict[str, Any]:
        job = {
            "id": uuid.uuid4().hex[:10],
            "url": url,
            "status": "queued",
            "progress": 0.0,
            "speed": "",
            "eta": "",
            "created": time.time(),
            "output": None,
            "error": None,
            "title": "",
            "bytes": 0,
            **kw,
        }
        self.jobs[job["id"]] = job
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job["status"] not in ("queued", "running"):
            return False
        job["status"] = "cancelled"
        if job.get("abort"):
            job["abort"].set()
        return True

    def analyze(self, url: str) -> dict[str, Any]:
        """Inspects metadata of a remote URL without downloading."""
        if not yt_dlp:
            raise MediaDownloaderError("yt-dlp is not installed. Add it to dependencies.")
        url = validate_url(url)
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "socket_timeout": 20,
        }
        if self.ffmpeg_exe and os.path.exists(self.ffmpeg_exe):
            opts["ffmpeg_location"] = os.path.dirname(self.ffmpeg_exe)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise MediaDownloaderError("No media information could be extracted from this URL.")
                if "entries" in info:
                    entries = [
                        {
                            "id": e.get("id", ""),
                            "title": e.get("title", "Untitled"),
                            "url": e.get("url") or e.get("webpage_url") or "",
                            "duration": float(e.get("duration") or 0.0),
                        }
                        for e in (info.get("entries") or [])[:50]
                        if e
                    ]
                    return {
                        "is_playlist": True,
                        "title": info.get("title", "Playlist"),
                        "entries_count": len(entries),
                        "entries": entries,
                        "url": url,
                    }
                return {
                    "is_playlist": False,
                    "title": info.get("title", "Untitled Video"),
                    "author": info.get("uploader") or info.get("channel", "Unknown"),
                    "duration": float(info.get("duration") or 0.0),
                    "thumbnail": info.get("thumbnail") or "",
                    "url": url,
                }
        except Exception as exc:
            raise MediaDownloaderError(f"Inspection error: {exc}") from exc

    def start_download(
        self,
        url: str,
        resolution: str | None = None,
        audio_only: bool = False,
        custom_name: str | None = None,
    ) -> dict[str, Any]:
        """Launches a background download job."""
        if not yt_dlp:
            raise MediaDownloaderError("yt-dlp is not installed. Install yt-dlp to download web media.")
        valid_url = validate_url(url)
        job = self._new_job(
            valid_url,
            resolution=resolution,
            audio_only=audio_only,
            custom_name=custom_name,
        )
        job["abort"] = threading.Event()

        def _worker():
            self._run_download_sync(job, valid_url, resolution, audio_only, custom_name)

        t = threading.Thread(target=_worker, daemon=True)
        self._threads[job["id"]] = t
        t.start()
        return {k: v for k, v in job.items() if k != "abort"}

    def _run_download_sync(
        self,
        job: dict[str, Any],
        url: str,
        resolution: str | None,
        audio_only: bool,
        custom_name: str | None,
    ) -> None:
        job["status"] = "running"
        self.folder.mkdir(parents=True, exist_ok=True)

        if custom_name:
            stem = safe_filename(custom_name)
            outtmpl = str(self.folder / f"{stem}.%(ext)s")
        else:
            outtmpl = str(self.folder / "%(title).60s-%(id)s.%(ext)s")

        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": outtmpl,
            "overwrites": True,
            "socket_timeout": 30,
            "retries": 3,
        }
        if self.ffmpeg_exe and os.path.exists(self.ffmpeg_exe):
            ydl_opts["ffmpeg_location"] = os.path.dirname(self.ffmpeg_exe)

        if audio_only:
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        elif resolution:
            height = "".join(filter(str.isdigit, resolution))
            if height:
                ydl_opts["format"] = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
            else:
                ydl_opts["format"] = "bestvideo+bestaudio/best"
            ydl_opts["merge_output_format"] = "mp4"
        else:
            ydl_opts["format"] = "bestvideo+bestaudio/best"
            ydl_opts["merge_output_format"] = "mp4"

        last_progress_time = 0.0

        def hook(d: dict[str, Any]):
            nonlocal last_progress_time
            if job.get("abort") and job["abort"].is_set():
                raise DownloadCancelledError("Download cancelled by user.")

            status = d.get("status")
            if status == "downloading":
                now = time.time()
                if now - last_progress_time > 0.15:
                    last_progress_time = now
                    got = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    job["progress"] = min(0.99, (got / total)) if total > 0 else 0.0
                    job["bytes"] = got

                    speed_bytes = d.get("speed") or 0
                    if speed_bytes > 1_048_576:
                        job["speed"] = f"{speed_bytes / 1_048_576:.1f} MB/s"
                    elif speed_bytes > 1024:
                        job["speed"] = f"{speed_bytes / 1024:.0f} KB/s"
                    else:
                        job["speed"] = ""

                    eta = d.get("eta")
                    if eta:
                        m, s = divmod(int(eta), 60)
                        job["eta"] = f"{m:02d}:{s:02d}"

            elif status == "finished":
                job["progress"] = 1.0
                job["speed"] = "Finalizing"

        ydl_opts["progress_hooks"] = [hook]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    raise MediaDownloaderError("Failed to download video.")
                if "requested_downloads" in info and info["requested_downloads"]:
                    final_path = Path(info["requested_downloads"][0]["filepath"])
                else:
                    final_path = Path(ydl.prepare_filename(info))
                    if audio_only and final_path.suffix != ".mp3":
                        final_path = final_path.with_suffix(".mp3")
                    elif not audio_only and not final_path.is_file():
                        candidate = final_path.with_suffix(".mp4")
                        if candidate.is_file():
                            final_path = candidate

                job["status"] = "done"
                job["progress"] = 1.0
                job["output"] = final_path.name
                job["title"] = info.get("title", final_path.stem)
                job["bytes"] = final_path.stat().st_size if final_path.is_file() else 0
        except DownloadCancelledError:
            job["status"] = "cancelled"
            job["error"] = "Download was cancelled."
        except Exception as exc:
            if job.get("abort") and job["abort"].is_set():
                job["status"] = "cancelled"
            else:
                job["status"] = "failed"
                job["error"] = str(exc)[:300]
        finally:
            self._threads.pop(job["id"], None)
