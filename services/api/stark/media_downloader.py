"""Media acquisition and web video downloader engine for STARK AI.

Supports safe downloading of public video content, playlists, and profiles using
bundled/installed yt-dlp and FFmpeg, directly into the workspace's media/video folder.
Includes hardened SSRF guards, DNS resolution & rebinding protection, redirect guards,
filename sanitization, persistent job management, queue concurrency, crash recovery,
disk space checks, partial-file cleanup, and E-STOP cancellation.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import html
import ipaddress
import logging
import os
import re
import shutil
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
import urllib.error
import urllib.request
from urllib.parse import urlsplit

logger = logging.getLogger("stark.media_downloader")

try:
    import yt_dlp
    import yt_dlp.networking.common as _ytdlp_common
    import yt_dlp.networking._urllib as _ytdlp_urllib
except ImportError:
    yt_dlp = None
    _ytdlp_common = None
    _ytdlp_urllib = None

_INVALID_FS_CHARS = '<>:"/\\|?*'
_DISALLOWED_PORTS = {21, 22, 23, 25, 53, 69, 110, 135, 139, 143, 445, 1433, 1521, 2375, 2376, 3306, 3389, 5432, 6379, 8737, 3737, 9200, 11211, 27017}
_METADATA_IPS = {
    "169.254.169.254",   # AWS, GCP, Azure, OpenStack instance metadata
    "100.100.100.200",   # Alibaba Cloud metadata
    "fd00:ec2::254",     # AWS IPv6 metadata
}
_DISALLOWED_DOMAINS = {
    "localhost",
    "metadata.google.internal",
    "metadata.nic.google",
    "instance-data",
    "metadata",
}


class MediaDownloaderError(Exception):
    """General media download or analysis failure."""
    pass


class DownloadCancelledError(Exception):
    """Raised when a download is actively cancelled or interrupted."""
    pass


def safe_filename(name: str) -> str:
    """Strips directory components and invalid filesystem characters from a filename."""
    base = os.path.basename(html.unescape(str(name or "")).replace("\\", "/"))
    clean = "".join("_" if (c in _INVALID_FS_CHARS or ord(c) < 32) else c for c in base).strip(" .")
    # Collapse multiple underscores
    clean = re.sub(r"_+", "_", clean)
    return clean[:120] or "video"


def resolve_safe_path(target_folder: Path, filename: str) -> Path:
    """Ensures the destination path stays strictly within the target folder (prevents directory traversal)."""
    clean_name = safe_filename(filename)
    dest = (target_folder / clean_name).resolve()
    base = target_folder.resolve()
    try:
        dest.relative_to(base)
    except ValueError:
        raise MediaDownloaderError(f"Path traversal detected: {filename}") from None
    return dest


def check_disk_space(target_folder: Path, min_mb: int = 500) -> None:
    """Verifies that the target volume has sufficient free space."""
    try:
        target_folder.mkdir(parents=True, exist_ok=True)
        _, _, free = shutil.disk_usage(target_folder)
        free_mb = free / (1024 * 1024)
        if free_mb < min_mb:
            raise MediaDownloaderError(f"Insufficient disk space ({free_mb:.0f} MB free, {min_mb} MB required).")
    except OSError as exc:
        logger.warning(f"Failed to check disk usage: {exc}")


def _is_ip_allowed(ip_str: str) -> bool:
    """Checks whether an IP address is a safe, publicly routable destination."""
    clean_ip = str(ip_str or "").strip().lower().strip("[]")
    if clean_ip in _METADATA_IPS:
        return False
    try:
        ip = ipaddress.ip_address(clean_ip)
        # Check IPv4 mapped in IPv6 (e.g. ::ffff:127.0.0.1 or ::ffff:169.254.169.254)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            if not _is_ip_allowed(str(ip.ipv4_mapped)):
                return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
        # Reject carrier-grade NAT (100.64.0.0/10)
        if isinstance(ip, ipaddress.IPv4Address):
            cgnat = ipaddress.ip_network("100.64.0.0/10")
            if ip in cgnat:
                return False
        return True
    except ValueError:
        return False


def validate_url(url: str, resolve_dns: bool = True) -> str:
    """Validates public HTTP(S) source URL and guards against SSRF, internal services, and metadata IPs."""
    clean = (url or "").strip()
    if not clean or any(ch.isspace() for ch in clean):
        raise MediaDownloaderError("Please provide a valid http(s) URL.")
    if "://" not in clean:
        clean = "https://" + clean

    parsed = urlsplit(clean)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise MediaDownloaderError("Only http:// and https:// URLs are supported.")

    hostname = (parsed.hostname or "").lower().strip()
    if not hostname:
        raise MediaDownloaderError("Invalid URL: missing hostname.")

    if parsed.port and parsed.port in _DISALLOWED_PORTS:
        raise MediaDownloaderError(f"Connections to restricted port {parsed.port} are forbidden.")

    # Reject known internal/cloud hostnames
    if (
        hostname in _DISALLOWED_DOMAINS
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
        or hostname.endswith(".corp")
        or hostname.endswith(".lan")
        or hostname.endswith(".onion")
    ):
        raise MediaDownloaderError("Access to internal, local, or cloud metadata domains is blocked.")

    # If hostname is an IP literal, validate immediately
    try:
        ip = ipaddress.ip_address(hostname)
        if not _is_ip_allowed(str(ip)):
            raise MediaDownloaderError("Access to internal, private, or metadata IP addresses is forbidden.")
        return clean
    except ValueError:
        pass

    # Resolve hostname via DNS and check every resolved IPv4/IPv6 address
    if resolve_dns:
        try:
            port = parsed.port or (443 if scheme == "https" else 80)
            addr_info = socket.getaddrinfo(hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
            resolved_ips = {item[4][0] for item in addr_info}
            if not resolved_ips:
                raise MediaDownloaderError(f"Could not resolve domain: {hostname}")
            for ip_str in resolved_ips:
                if not _is_ip_allowed(ip_str):
                    raise MediaDownloaderError(
                        f"SSRF protection: domain {hostname} resolves to restricted address {ip_str}"
                    )
        except socket.gaierror as exc:
            raise MediaDownloaderError(f"DNS resolution failed for {hostname}: {exc}") from None

    return clean


# ------------------------------------------------------------------ yt-dlp SSRF & Rebinding Hooks
_YTDLP_HOOKS_INSTALLED = False
_HOOK_LOCK = threading.Lock()
_ORIG_CREATE_CONNECTION = socket.create_connection
_SSRF_GUARD_ACTIVE = threading.local()


@contextmanager
def ssrf_socket_guard():
    """Activates socket-level SSRF and DNS rebinding protections for the current thread."""
    _SSRF_GUARD_ACTIVE.active = True
    try:
        yield
    finally:
        _SSRF_GUARD_ACTIVE.active = False


def _safe_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, *args, **kwargs):
    """Enforces SSRF and DNS rebinding protections during media acquisition operations.
    Directly connects to verified publicly routable destination IP, eliminating TOCTOU DNS rebinding races.
    """
    if not getattr(_SSRF_GUARD_ACTIVE, "active", False):
        return _ORIG_CREATE_CONNECTION(address, timeout=timeout, source_address=source_address, *args, **kwargs)

    if not (isinstance(address, (list, tuple)) and len(address) >= 2):
        return _ORIG_CREATE_CONNECTION(address, timeout=timeout, source_address=source_address, *args, **kwargs)

    host, port = address[0], address[1]
    if isinstance(port, int) and port in _DISALLOWED_PORTS:
        raise ConnectionRefusedError(f"Connection to restricted port {port} is blocked by SSRF defense.")

    host_str = str(host).lower().strip("[]")

    # Reject known internal/cloud hostnames immediately
    if (
        host_str in _DISALLOWED_DOMAINS
        or host_str.endswith(".localhost")
        or host_str.endswith(".local")
        or host_str.endswith(".internal")
        or host_str.endswith(".corp")
        or host_str.endswith(".lan")
        or host_str.endswith(".onion")
    ):
        raise ConnectionRefusedError(f"SSRF defense blocked connection to internal domain {host_str}")

    # Resolve destination using getaddrinfo
    try:
        addr_infos = socket.getaddrinfo(host_str, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConnectionRefusedError(f"DNS resolution failed for {host_str}: {exc}") from exc

    if not addr_infos:
        raise ConnectionRefusedError(f"No address records found for {host_str}")

    # Validate all resolved IPs and filter only safe, publicly routable destinations
    safe_candidates = []
    for res in addr_infos:
        cand_ip = res[4][0]
        if _is_ip_allowed(cand_ip):
            safe_candidates.append(res)
        else:
            logger.warning(f"SSRF guard filtered out restricted IP {cand_ip} for destination {host_str}")

    if not safe_candidates:
        raise ConnectionRefusedError(
            f"DNS rebinding blocked: destination {host_str} resolved only to restricted addresses"
        )

    # Connect directly to the validated sockaddr, avoiding any secondary DNS lookup (TOCTOU elimination)
    last_err = None
    for res in safe_candidates:
        af, socktype, proto, canonname, sa = res
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)

            # Final validation: check actual connected peer IP on socket
            peer_ip = sock.getpeername()[0]
            if not _is_ip_allowed(peer_ip):
                sock.close()
                raise ConnectionRefusedError(
                    f"SSRF guard: connected peer IP {peer_ip} is restricted!"
                )

            return sock
        except Exception as err:
            last_err = err
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    if last_err:
        raise last_err
    raise ConnectionRefusedError(f"Failed to connect to validated destination {host_str}:{port}")


def install_ytdlp_ssrf_guards() -> None:
    """Installs runtime SSRF interceptors into yt-dlp dispatchers and socket connections."""
    global _YTDLP_HOOKS_INSTALLED
    with _HOOK_LOCK:
        if _YTDLP_HOOKS_INSTALLED:
            return

        # Install socket connection guard against DNS rebinding
        socket.create_connection = _safe_create_connection

        # Also hook standard library urllib redirect handler
        _orig_urllib_redirect = urllib.request.HTTPRedirectHandler.redirect_request

        def _safe_urllib_redirect(self, req, fp, code, msg, headers, newurl):
            if getattr(_SSRF_GUARD_ACTIVE, "active", False) and newurl:
                try:
                    validate_url(newurl, resolve_dns=True)
                except Exception as exc:
                    raise urllib.error.URLError(f"SSRF redirect blocked: {exc}") from exc
            return _orig_urllib_redirect(self, req, fp, code, msg, headers, newurl)

        urllib.request.HTTPRedirectHandler.redirect_request = _safe_urllib_redirect

        if _ytdlp_common and _ytdlp_urllib:
            orig_send = _ytdlp_common.RequestDirector.send

            def safe_send(self, request):
                if hasattr(request, "url") and request.url:
                    try:
                        validate_url(request.url, resolve_dns=True)
                    except Exception as exc:
                        raise _ytdlp_common.TransportError(f"SSRF blocked: {exc}") from exc
                return orig_send(self, request)

            _ytdlp_common.RequestDirector.send = safe_send

            orig_redirect = _ytdlp_urllib.RedirectHandler.redirect_request

            def safe_redirect(self, req, fp, code, msg, headers, newurl):
                if newurl:
                    try:
                        validate_url(newurl, resolve_dns=True)
                    except Exception as exc:
                        raise _ytdlp_urllib.TransportError(f"SSRF redirect blocked: {exc}") from exc
                return orig_redirect(self, req, fp, code, msg, headers, newurl)

            _ytdlp_urllib.RedirectHandler.redirect_request = safe_redirect

        _YTDLP_HOOKS_INSTALLED = True
        logger.info("yt-dlp SSRF request, redirect, and socket rebinding guards active.")


def ytdlp_available() -> bool:
    return yt_dlp is not None or shutil.which("yt-dlp") is not None


# ------------------------------------------------------------------ MediaDownloader Engine
class MediaDownloader:
    """Manages background download tasks with persistent state, queue concurrency, and cancellation."""

    def __init__(
        self,
        target_folder: Path,
        ffmpeg_exe: str | None = None,
        db: Any = None,
        kernel: Any = None,
        max_concurrent: int = 2,
    ) -> None:
        self.folder = target_folder
        self.ffmpeg_exe = ffmpeg_exe
        self.db = db
        self.kernel = kernel
        self.max_concurrent = max(1, max_concurrent)
        self.jobs: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._abort_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._queue_running = True

        install_ytdlp_ssrf_guards()
        self._restore_jobs()

        # Start background queue processor
        self._queue_thread = threading.Thread(target=self._process_queue_loop, daemon=True)
        self._queue_thread.start()

    def _restore_jobs(self) -> None:
        """Restores job records from database, sets up cancellation events, and cleans up interrupted states."""
        if not self.db:
            return
        try:
            from .core.db import DownloadJob
            from sqlalchemy import select

            with self.db.session() as s:
                rows = s.scalars(select(DownloadJob).order_by(DownloadJob.created_at.desc()).limit(50)).all()
                for r in rows:
                    job_dict = {
                        "id": r.id,
                        "url": r.url,
                        "title": r.title,
                        "status": r.status,
                        "progress": r.progress,
                        "speed": r.speed,
                        "eta": r.eta,
                        "bytes": r.bytes_downloaded,
                        "total_bytes": r.total_bytes,
                        "output": r.output_file,
                        "error": r.error,
                        "resolution": r.resolution,
                        "audio_only": r.audio_only,
                        "is_playlist": r.is_playlist,
                        "playlist_count": r.playlist_count,
                        "playlist_index": r.playlist_index,
                        "files": list(getattr(r, "files", None) or []),
                        "created": r.created_at.timestamp() if r.created_at else time.time(),
                        "active_files": set(),
                    }
                    # If job was in-flight when app crashed/restarted, transition to interrupted
                    if job_dict["status"] == "running":
                        job_dict["status"] = "interrupted"
                        job_dict["error"] = "Interrupted by application restart."
                        r.status = "interrupted"
                        r.error = "Interrupted by application restart."

                    self.jobs[r.id] = job_dict
                    # Restored jobs (especially queued jobs) MUST have an abort Event so workers can run them
                    self._abort_events[r.id] = threading.Event()
        except Exception as exc:
            logger.warning(f"Could not restore download jobs from database: {exc}")

    def _persist_job(self, job: dict[str, Any]) -> None:
        """Persists job updates to the relational database if available."""
        if not self.db:
            return
        try:
            from .core.db import DownloadJob

            with self.db.session() as s:
                row = s.get(DownloadJob, job["id"])
                now = datetime.now(timezone.utc)
                files_list = list(job.get("files") or [])
                if row:
                    row.status = job["status"]
                    row.progress = float(job.get("progress") or 0.0)
                    row.speed = str(job.get("speed") or "")
                    row.eta = str(job.get("eta") or "")
                    row.bytes_downloaded = int(job.get("bytes") or 0)
                    row.total_bytes = int(job.get("total_bytes") or 0)
                    row.output_file = job.get("output")
                    row.error = job.get("error")
                    row.title = str(job.get("title") or "")
                    row.playlist_count = int(job.get("playlist_count") or 0)
                    row.playlist_index = int(job.get("playlist_index") or 0)
                    if hasattr(row, "files"):
                        row.files = files_list
                    row.updated_at = now
                else:
                    created_dt = datetime.fromtimestamp(job["created"], timezone.utc) if job.get("created") else now
                    row = DownloadJob(
                        id=job["id"],
                        url=job["url"],
                        title=str(job.get("title") or ""),
                        status=job["status"],
                        progress=float(job.get("progress") or 0.0),
                        speed=str(job.get("speed") or ""),
                        eta=str(job.get("eta") or ""),
                        bytes_downloaded=int(job.get("bytes") or 0),
                        total_bytes=int(job.get("total_bytes") or 0),
                        output_file=job.get("output"),
                        error=job.get("error"),
                        resolution=job.get("resolution"),
                        audio_only=bool(job.get("audio_only", False)),
                        custom_name=job.get("custom_name"),
                        is_playlist=bool(job.get("is_playlist", False)),
                        playlist_count=int(job.get("playlist_count") or 0),
                        playlist_index=int(job.get("playlist_index") or 0),
                        created_at=created_dt,
                        updated_at=now,
                    )
                    if hasattr(row, "files"):
                        row.files = files_list
                    s.add(row)
        except Exception as exc:
            logger.debug(f"Failed to persist download job {job.get('id')}: {exc}")

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j.get("created", 0), reverse=True)[:50]
            return [{k: (list(v) if isinstance(v, set) else v) for k, v in j.items() if k != "abort"} for j in jobs]

    def _new_job(self, url: str, **kw: Any) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:10]
        job = {
            "id": job_id,
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
            "total_bytes": 0,
            "is_playlist": False,
            "playlist_count": 0,
            "playlist_index": 0,
            "files": [],
            "active_files": set(),
            "active_temp_files": set(),
            "completed_files": set(),
            **kw,
        }
        with self._lock:
            self.jobs[job["id"]] = job
            self._abort_events[job["id"]] = threading.Event()
        self._persist_job(job)
        return job

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job or job["status"] not in ("queued", "running"):
                return False
            job["status"] = "cancelled"
            job["error"] = "Cancelled by user."
            abort = self._abort_events.get(job_id)
            if abort:
                abort.set()
        self._cleanup_partial_files(job_id)
        self._persist_job(job)
        return True

    def _cleanup_partial_files(self, job_id: str, *args, **kwargs) -> None:
        """Removes temporary and incomplete downloads belonging strictly to this job.
        Strict per-job file ownership: never deletes unrelated files, never deletes completed
        playlist items, and never relies on substring filename matching.
        """
        try:
            if not self.folder.exists():
                return
            with self._lock:
                job = self.jobs.get(job_id)
                if not job:
                    return

                # Explicitly owned temporary / in-flight files for this specific job
                tracked_temps = set(job.get("active_temp_files") or set())
                tracked_temps.update(job.get("active_files") or set())
                for ext in (".part", ".temp", ".ytdl"):
                    tracked_temps.add(str((self.folder / f"{job_id}{ext}").resolve()))

                completed_files = {str(Path(p).resolve()) for p in (job.get("completed_files") or set())}
                for f in (job.get("files") or []):
                    completed_files.add(str((self.folder / f).resolve()))
                if job.get("output") and job.get("status") == "done":
                    completed_files.add(str((self.folder / job["output"]).resolve()))

                # Collect protected paths belonging to all OTHER jobs
                protected_paths = set()
                for jid, other in self.jobs.items():
                    if jid != job_id:
                        for cf in other.get("completed_files", set()):
                            protected_paths.add(str(Path(cf).resolve()))
                        for af in other.get("active_temp_files", set()):
                            protected_paths.add(str(Path(af).resolve()))
                        for af in other.get("active_files", set()):
                            protected_paths.add(str(Path(af).resolve()))
                        for f in other.get("files", []):
                            protected_paths.add(str((self.folder / f).resolve()))
                        if other.get("output"):
                            protected_paths.add(str((self.folder / other["output"]).resolve()))

            target_folder_res = self.folder.resolve()

            # Iterate ONLY over explicitly registered in-flight temp paths for THIS job
            for fpath_str in list(tracked_temps):
                p = Path(fpath_str).resolve()
                p_str = str(p)

                # Never delete a file that has been completed and verified for this job
                if p_str in completed_files:
                    continue
                # Never delete a file owned or protected by another job
                if p_str in protected_paths:
                    continue
                # Ensure the path is strictly within our target download folder
                try:
                    p.relative_to(target_folder_res)
                except ValueError:
                    continue

                # Delete incomplete/part files
                if p.suffix in (".part", ".ytdl", ".temp") or p.name.endswith(".part"):
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass
                elif p.is_file() and p_str not in completed_files:
                    # Incomplete media file that was interrupted before completion
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass

                # Also delete companion part/temp variants of this tracked path
                for ext in (".part", ".ytdl", ".temp"):
                    part_p = p.with_name(p.name + ext)
                    part_str = str(part_p)
                    if (
                        part_p.is_file()
                        and part_str not in completed_files
                        and part_str not in protected_paths
                    ):
                        try:
                            part_p.unlink(missing_ok=True)
                        except OSError:
                            pass
        except Exception as exc:
            logger.debug(f"Cleanup error for {job_id}: {exc}")

    def analyze(self, url: str) -> dict[str, Any]:
        """Inspects metadata of a remote URL without downloading."""
        if not yt_dlp:
            raise MediaDownloaderError("yt-dlp is not installed. Add it to dependencies.")
        url = validate_url(url, resolve_dns=True)
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
            with ssrf_socket_guard():
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
        except MediaDownloaderError:
            raise
        except Exception as exc:
            raise MediaDownloaderError(f"Inspection error: {exc}") from exc

    def start_download(
        self,
        url: str,
        resolution: str | None = None,
        audio_only: bool = False,
        custom_name: str | None = None,
    ) -> dict[str, Any]:
        """Validates inputs and enqueues a background download job."""
        if not yt_dlp:
            raise MediaDownloaderError("yt-dlp is not installed. Install yt-dlp to download web media.")
        valid_url = validate_url(url, resolve_dns=True)
        check_disk_space(self.folder, min_mb=500)

        job = self._new_job(
            valid_url,
            resolution=resolution,
            audio_only=audio_only,
            custom_name=custom_name,
        )
        return {k: v for k, v in job.items() if k != "abort"}

    def _process_queue_loop(self) -> None:
        """Background worker thread that respects concurrency limits and dispatches queued downloads."""
        while self._queue_running:
            try:
                # Check emergency stop
                if self.kernel and getattr(self.kernel.perms, "emergency_stop", False):
                    with self._lock:
                        for jid, job in self.jobs.items():
                            if job["status"] in ("queued", "running"):
                                job["status"] = "cancelled"
                                job["error"] = "Emergency stop engaged."
                                ev = self._abort_events.get(jid)
                                if ev:
                                    ev.set()
                                self._persist_job(job)
                    time.sleep(1.0)
                    continue

                with self._lock:
                    running_count = sum(1 for j in self.jobs.values() if j["status"] == "running")
                    candidate = None
                    if running_count < self.max_concurrent:
                        queued = [j for j in self.jobs.values() if j["status"] == "queued"]
                        if queued:
                            candidate = min(queued, key=lambda j: j["created"])

                if candidate:
                    candidate_id = candidate["id"]
                    t = threading.Thread(
                        target=self._run_download_sync,
                        args=(candidate_id,),
                        daemon=True,
                    )
                    with self._lock:
                        self._threads[candidate_id] = t
                    t.start()
            except Exception as exc:
                logger.error(f"Queue processor error: {exc}")
            time.sleep(0.5)

    def _run_download_sync(self, job_id: str) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
            abort = self._abort_events.get(job_id)
            if not job or not abort or abort.is_set() or job.get("status") in ("cancelled", "interrupted"):
                return
            job["status"] = "running"
        self._persist_job(job)
        self.folder.mkdir(parents=True, exist_ok=True)

        url = job["url"]
        custom_name = job.get("custom_name")
        audio_only = bool(job.get("audio_only"))
        resolution = job.get("resolution")

        if custom_name:
            stem = safe_filename(custom_name)
            outtmpl = str(self.folder / f"{stem}.%(ext)s")
        else:
            outtmpl = str(self.folder / "%(title).60s-%(id)s.%(ext)s")

        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": outtmpl,
            "overwrites": False,  # collision safe
            "socket_timeout": 30,
            "retries": 3,
            "max_filesize": 2 * 1024 * 1024 * 1024,  # 2 GB safety ceiling
            "external_downloader": None,
            "compat_opts": ["no-external-downloader"],
            "external_downloader_args": None,
        }
        if self.ffmpeg_exe and os.path.exists(self.ffmpeg_exe):
            ffmpeg_dir = os.path.dirname(os.path.abspath(self.ffmpeg_exe))
            if os.path.isdir(ffmpeg_dir):
                ydl_opts["ffmpeg_location"] = ffmpeg_dir

        if audio_only:
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ]
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
            if abort.is_set() or (self.kernel and getattr(self.kernel.perms, "emergency_stop", False)):
                raise DownloadCancelledError("Download cancelled.")

            status = d.get("status")
            filename = d.get("filename")
            if filename:
                f_abs = str(Path(filename).resolve())
                job.setdefault("active_temp_files", set()).add(f_abs)
                job.setdefault("active_files", set()).add(f_abs)
            tmp = d.get("tmpfilename")
            if tmp:
                t_abs = str(Path(tmp).resolve())
                job.setdefault("active_temp_files", set()).add(t_abs)
                job.setdefault("active_files", set()).add(t_abs)

            info_dict = d.get("info_dict") or {}
            if "id" in info_dict and not job.get("video_id"):
                job["video_id"] = str(info_dict["id"])

            p_idx = info_dict.get("playlist_index") or d.get("playlist_index")
            p_cnt = info_dict.get("playlist_count") or d.get("playlist_count") or info_dict.get("n_entries")
            if p_idx:
                job["is_playlist"] = True
                job["playlist_index"] = int(p_idx)
            if p_cnt:
                job["is_playlist"] = True
                job["playlist_count"] = int(p_cnt)

            if status == "downloading":
                now = time.time()
                if now - last_progress_time > 0.2:
                    last_progress_time = now
                    got = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    job["progress"] = min(0.99, (got / total)) if total > 0 else 0.0
                    job["bytes"] = got
                    job["total_bytes"] = total

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

                    # Update DB periodically
                    self._persist_job(job)

            elif status == "finished":
                job["progress"] = 0.99
                job["speed"] = "Finalizing"
                if filename:
                    p = Path(filename).resolve()
                    if p.is_file() and p.stat().st_size > 0:
                        p_abs = str(p)
                        job.setdefault("completed_files", set()).add(p_abs)
                        job.setdefault("active_temp_files", set()).discard(p_abs)
                        if p.name not in job.setdefault("files", []):
                            job["files"].append(p.name)
                        for ext in (".part", ".ytdl", ".temp"):
                            part_p = p.with_name(p.name + ext)
                            job.setdefault("active_temp_files", set()).discard(str(part_p.resolve()))

        ydl_opts["progress_hooks"] = [hook]

        try:
            with ssrf_socket_guard():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                if not info:
                    raise MediaDownloaderError("Failed to extract media.")

                downloaded_files: list[str] = list(job.get("files") or [])
                entries = info.get("entries")
                if entries:
                    job["is_playlist"] = True
                    job["playlist_count"] = len(entries)
                    for entry in entries:
                        if not entry:
                            continue
                        epaths = []
                        if "requested_downloads" in entry:
                            for rd in entry["requested_downloads"]:
                                if rd.get("filepath"):
                                    epaths.append(Path(rd["filepath"]))
                        if not epaths:
                            ep = ydl.prepare_filename(entry)
                            candidate = Path(ep)
                            if audio_only and candidate.with_suffix(".mp3").is_file():
                                epaths.append(candidate.with_suffix(".mp3"))
                            elif candidate.is_file():
                                epaths.append(candidate)
                            elif candidate.with_suffix(".mp4").is_file():
                                epaths.append(candidate.with_suffix(".mp4"))

                        for p in epaths:
                            if p.is_file() and p.stat().st_size > 0:
                                if p.name not in downloaded_files:
                                    downloaded_files.append(p.name)
                                p_res = str(p.resolve())
                                job.setdefault("completed_files", set()).add(p_res)
                                job.setdefault("active_temp_files", set()).discard(p_res)
                                job.setdefault("active_files", set()).add(p_res)

                elif "requested_downloads" in info and info["requested_downloads"]:
                    for rd in info["requested_downloads"]:
                        fp = rd.get("filepath")
                        if fp:
                            p = Path(fp)
                            if p.is_file() and p.stat().st_size > 0:
                                if p.name not in downloaded_files:
                                    downloaded_files.append(p.name)
                                p_res = str(p.resolve())
                                job.setdefault("completed_files", set()).add(p_res)
                                job.setdefault("active_temp_files", set()).discard(p_res)
                                job.setdefault("active_files", set()).add(p_res)

                if not downloaded_files:
                    final_path = Path(ydl.prepare_filename(info))
                    if audio_only and final_path.suffix != ".mp3":
                        candidate = final_path.with_suffix(".mp3")
                        if candidate.is_file():
                            final_path = candidate
                    elif not audio_only and not final_path.is_file():
                        candidate = final_path.with_suffix(".mp4")
                        if candidate.is_file():
                            final_path = candidate
                    if final_path.is_file() and final_path.stat().st_size > 0:
                        downloaded_files.append(final_path.name)
                        p_res = str(final_path.resolve())
                        job.setdefault("completed_files", set()).add(p_res)
                        job.setdefault("active_temp_files", set()).discard(p_res)
                        job.setdefault("active_files", set()).add(p_res)

                # Verify all expected files exist and have non-zero size
                if not downloaded_files:
                    raise MediaDownloaderError("Output file was not created or is empty.")

                for fname in downloaded_files:
                    fpath = self.folder / fname
                    if not fpath.is_file() or fpath.stat().st_size == 0:
                        raise MediaDownloaderError(f"Downloaded file {fname} is missing or empty.")

                job["status"] = "done"
                job["progress"] = 1.0
                job["output"] = downloaded_files[0]
                job["files"] = downloaded_files
                job["title"] = info.get("title", Path(downloaded_files[0]).stem)
                job["bytes"] = sum((self.folder / f).stat().st_size for f in downloaded_files if (self.folder / f).is_file())
                job["total_bytes"] = job["bytes"]
                if job.get("is_playlist"):
                    job["playlist_index"] = len(downloaded_files)

                # Publish notification to bus for each completed file
                if self.kernel and hasattr(self.kernel, "bus"):
                    for fname in downloaded_files:
                        fp = self.folder / fname
                        self.kernel.bus.publish("media.downloaded", {
                            "job_id": job_id,
                            "filename": fname,
                            "title": job["title"],
                            "size": fp.stat().st_size if fp.is_file() else 0,
                            "is_playlist": job.get("is_playlist", False),
                        })

        except DownloadCancelledError:
            job["status"] = "cancelled"
            job["error"] = "Download was cancelled."
            self._cleanup_partial_files(job_id)
        except Exception as exc:
            if abort.is_set():
                job["status"] = "cancelled"
                job["error"] = "Download was cancelled."
            else:
                job["status"] = "failed"
                job["error"] = str(exc)[:300]
            self._cleanup_partial_files(job_id)
        finally:
            self._persist_job(job)
            with self._lock:
                self._threads.pop(job_id, None)
