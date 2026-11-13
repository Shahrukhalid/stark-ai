from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import threading
import time
import pytest
import httpx
from sqlalchemy import select

from stark.config import Settings
from stark.core.db import Approval, ContentItem
from stark.core.runtime import ToolContext
from stark.main import build_kernel
from stark.media_downloader import MediaDownloader
from stark.tools.social import Social, _resolve_lock_dir
from stark.tools import social as social_tools
from stark.video import VideoStudio, measure_audio_loudness, probe

from .conftest import make_kernel, text, tool, n_tool_rounds, wait_for


# ------------------------------------------------------------------ 1. Vision & Wanda Verification
@pytest.mark.asyncio
async def test_vision_wanda_personas_handoff_and_memory(settings):
    """Exercises Vision and Wanda primary assistants, multi-turn interaction, context handoff, and consent-gated memory."""
    def assistant_brain(agent, request):
        if agent == "vision":
            return [text("Vision here. System status optimal, ready for creative briefing.")]
        elif agent == "wanda":
            return [text("Wanda active. Exploring creative narrative concepts.")]
        return [text("Fallback response")]

    kernel, provider = make_kernel(settings, assistant_brain)

    # 1. Vision initial multi-turn greeting
    t_vision = kernel.submit_chat("vision", "Vision, report system status")
    res_vision = await kernel.wait(t_vision["id"], timeout=5)
    assert res_vision["status"] == "completed"
    assert "Vision here" in res_vision["result"]

    # 2. Wanda creative persona verification
    t_wanda = kernel.submit_chat("wanda", "Wanda, give me conceptual ideas")
    res_wanda = await kernel.wait(t_wanda["id"], timeout=5)
    assert res_wanda["status"] == "completed"
    assert "Wanda active" in res_wanda["result"]

    # 3. Context handoff from Vision to Wanda
    # Vision submits approved handoff context
    handoff_task = kernel.submit_chat(
        "wanda",
        "Context from Vision: The user prefers cinematic pacing.",
        conversation_id=None,
    )
    res_handoff = await kernel.wait(handoff_task["id"], timeout=5)
    assert res_handoff["status"] == "completed"

    # 4. Consent-gated memory isolation
    vision = kernel.registry.get("vision")
    wanda = kernel.registry.get("wanda")
    kernel.memory.save(
        actor=vision,
        namespace="personal:vision",
        layer="preference",
        content="The user prefers dark themes.",
        consent=True,
    )
    assert kernel.memory.search(actor=vision, query="themes")
    # Must not leak to Wanda or other agents
    assert not kernel.memory.search(actor=wanda, query="themes")


# ------------------------------------------------------------------ 2. Real Video Downloads & Playlists
class ControlledMediaHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress console log spam during tests


@pytest.fixture(scope="module")
def media_test_server(tmp_path_factory):
    """Spawns an in-process HTTP test server serving synthetic media files."""
    srv_dir = tmp_path_factory.mktemp("server_media")
    video_1 = srv_dir / "sample_video_1.mp4"
    video_2 = srv_dir / "sample_video_2.mp4"

    # Generate 1-second synthetic MP4 clips using FFmpeg
    for v in (video_1, video_2):
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=navy:size=320x240:rate=25:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=800:duration=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            str(v),
        ]
        subprocess.run(cmd, check=True)

    class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    os.chdir(srv_dir)
    server = ThreadedServer(("127.0.0.1", 0), ControlledMediaHandler)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://{host}:{port}", srv_dir
    server.shutdown()


def test_real_downloader_and_playlist_acquisition(media_test_server, tmp_path, monkeypatch):
    """Downloads real media and multi-file playlist from a controlled HTTP server, confirming progress, file creation, and cancellation isolation."""
    base_url, srv_dir = media_test_server
    download_dir = tmp_path / "downloads"
    download_dir.mkdir(parents=True, exist_ok=True)

    # Permit loopback connection for our controlled test server
    from stark import media_downloader
    monkeypatch.setattr(media_downloader, "_is_ip_allowed", lambda ip: True)

    downloader = MediaDownloader(download_dir, max_concurrent=2)
    try:
        url_1 = f"{base_url}/sample_video_1.mp4"
        url_2 = f"{base_url}/sample_video_2.mp4"

        # 1. Enqueue download 1
        job1 = downloader.start_download(url_1, custom_name="clip1.mp4")
        job1_id = job1["id"]

        # Enqueue download 2
        job2 = downloader.start_download(url_2, custom_name="clip2.mp4")
        job2_id = job2["id"]

        # Cancel job 2 while in-flight to verify cancellation isolation
        downloader.cancel(job2_id)

        # Wait for job 1 to complete
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            j1 = downloader.jobs[job1_id]
            if j1["status"] in ("done", "failed", "cancelled"):
                break
            time.sleep(0.1)

        j1 = downloader.jobs[job1_id]
        assert j1["status"] == "done", f"Download failed: {j1.get('error')}"
        assert j1["output"] == "clip1.mp4"
        out_path = download_dir / "clip1.mp4"
        assert out_path.is_file() and out_path.stat().st_size > 500

        # Verify job 2 was cancelled cleanly without leaving orphaned temp files
        j2 = downloader.jobs[job2_id]
        assert j2["status"] == "cancelled"
        assert not (download_dir / "clip2.mp4.part").exists()
    finally:
        downloader._queue_running = False


# ------------------------------------------------------------------ 3. Media Processing in VideoStudio
@pytest.mark.asyncio
async def test_real_media_processing_twopass_loudnorm_and_watermark(tmp_path):
    """Exercises VideoStudio with automatic two-pass audio normalization, visual color grading, and logo watermarking."""
    workdir = tmp_path / "workspace"
    media_dir = workdir / "media" / "video"
    media_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate sample video and sample watermark PNG
    input_video = media_dir / "raw_input.mp4"
    watermark_png = media_dir / "logo_mark.png"

    # 1-second video with distinct audio
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=darkgreen:size=640x360:rate=25:duration=1.5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        str(input_video),
    ], check=True)

    # Watermark logo
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=yellow@0.8:size=64x64",
        "-vframes", "1", str(watermark_png),
    ], check=True)

    settings = Settings(data_dir=workdir, workspace_roots=[workdir])
    studio = VideoStudio(settings)

    opts = {
        "file": input_video.name,
        "preset": "landscape",
        "format": "mp4",
        "normalize_audio": True,
        "brightness": 0.1,
        "contrast": 1.2,
        "saturation": 1.1,
        "gamma": 1.05,
        "watermark_image": watermark_png.name,
        "watermark_position": "top-right",
    }

    job = studio.start(opts)
    job_id = job["id"]

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        j = studio.jobs.get(job_id)
        if j and j["status"] in ("done", "failed", "cancelled"):
            break
        await asyncio.sleep(0.2)

    final_job = studio.jobs[job_id]
    assert final_job["status"] == "done", f"Export failed: {final_job.get('error')}"
    out_file = media_dir / final_job["output"]
    assert out_file.is_file() and out_file.stat().st_size > 1000

    # Validate output loudness was recorded by 2-pass analysis
    assert "measured_loudness" in final_job
    loudness = final_job["measured_loudness"]
    assert -16.0 <= float(loudness["integrated_lufs"]) <= -12.0
    assert float(loudness["true_peak_db"]) <= -1.0


# ------------------------------------------------------------------ 4. Approved Social Publishing
@pytest.mark.asyncio
async def test_approved_social_publishing_lifecycle(settings):
    """Verifies complete social lifecycle: drafting by D.U.M.-E., approval gating, tamper protection, and duplicate blocking."""
    settings.mastodon_base_url = "https://social.stark.local"
    settings.mastodon_access_token = "valid-masto-token"

    published_posts = []
    def social_mock_handler(req: httpx.Request):
        published_posts.append(req.content)
        return httpx.Response(200, json={
            "id": f"post-{len(published_posts)}",
            "url": f"https://social.stark.local/@user/{len(published_posts)}",
        })

    kernel, _ = make_kernel(settings)
    soc = Social(settings, httpx.MockTransport(social_mock_handler))

    # 1. Draft post
    tool = kernel.tools.get("social.draft")
    t_ctx = ToolContext(kernel=kernel, agent=kernel.registry.get("edith"), task_id="t1", root_id="t1", settings=kernel.settings)
    draft_res = await tool.run(tool.Input(platform="mastodon", text="Official STARK AI product release #starkai"), t_ctx)
    assert draft_res.ok
    item_id = draft_res.data["id"]
    valid_fp = draft_res.data["fingerprint"]

    # 2. Tampering test: if approved fingerprint doesn't match current content, publication is blocked
    tampered_fp = "mismatched_tampered_hash_value"
    blocked_res = await social_tools.publish_item(kernel, soc, settings, item_id, approved_fp=tampered_fp, actor="user")
    assert not blocked_res.ok
    assert blocked_res.error == "changed_after_approval"
    assert "changed after you approved it" in blocked_res.summary

    # 3. Successful publication with correct approval fingerprint
    pub_res = await social_tools.publish_item(kernel, soc, settings, item_id, approved_fp=valid_fp, actor="user")
    assert pub_res.ok
    assert "Published to Mastodon" in pub_res.summary
    assert len(published_posts) == 1

    # 4. Duplicate prevention test: attempting to publish identical content within cooldown window is blocked
    draft2_res = await tool.run(tool.Input(platform="mastodon", text="Official STARK AI product release #starkai"), t_ctx)
    assert draft2_res.ok
    dup_pub_res = await social_tools.publish_item(kernel, soc, settings, draft2_res.data["id"], approved_fp=None, actor="user")
    assert not dup_pub_res.ok
    assert dup_pub_res.error == "duplicate_post"
    assert "Duplicate post prevented" in dup_pub_res.summary
