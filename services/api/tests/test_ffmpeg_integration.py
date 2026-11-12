from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from stark import video


@pytest.fixture(scope="module")
def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        pytest.skip("FFmpeg is not installed on this system.")
    return exe


@pytest.fixture
def sample_video(tmp_path: Path, ffmpeg_bin: str) -> Path:
    """Generates a real 1-second synthetic video with video and audio tracks."""
    src = tmp_path / "sample_input.mp4"
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi",
        "-i", "testsrc=duration=1:size=320x240:rate=24",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=1",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        str(src),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Failed to generate test video: {res.stderr}"
    assert src.is_file() and src.stat().st_size > 0
    return src


def test_real_ffmpeg_color_grading(ffmpeg_bin: str, sample_video: Path, tmp_path: Path):
    """Executes real FFmpeg color grading with brightness, contrast, saturation, and gamma."""
    out = tmp_path / "color_graded.mp4"
    info = {"duration": 1.0, "width": 320, "height": 240, "audio": True}
    opts = {
        "preset": "landscape",
        "format": "mp4",
        "brightness": 0.08,
        "contrast": 1.15,
        "saturation": 1.25,
        "gamma": 1.10,
    }
    cmd = video.build_command(ffmpeg_bin, sample_video, out, info, opts, None)
    res = subprocess.run(cmd, cwd=sample_video.parent, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"FFmpeg color grading failed: {res.stderr}"
    assert out.is_file()
    assert out.stat().st_size > 1000


def test_real_ffmpeg_image_watermarking(ffmpeg_bin: str, sample_video: Path, tmp_path: Path):
    """Generates a real PNG image and applies an image overlay watermark via FFmpeg filter_complex."""
    # 1. Create a synthetic test PNG watermark
    logo = tmp_path / "test_logo.png"
    logo_cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi",
        "-i", "color=c=red@0.8:size=64x64",
        "-vframes", "1",
        str(logo),
    ]
    res_logo = subprocess.run(logo_cmd, capture_output=True, text=True, check=False)
    assert res_logo.returncode == 0 and logo.is_file()

    out = tmp_path / "image_watermarked.mp4"
    info = {"duration": 1.0, "width": 320, "height": 240, "audio": True}
    opts = {
        "preset": "landscape",
        "format": "mp4",
        "watermark_image": logo.name,
        "watermark_image_opacity": 0.75,
        "watermark_image_scale": 0.25,
        "watermark_position": "bottom-right",
    }
    cmd = video.build_command(ffmpeg_bin, sample_video, out, info, opts, None)
    res = subprocess.run(cmd, cwd=sample_video.parent, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"FFmpeg image watermark overlay failed: {res.stderr}"
    assert out.is_file()
    assert out.stat().st_size > 1000


def test_real_ffmpeg_loudnorm_audio_processing(ffmpeg_bin: str, sample_video: Path, tmp_path: Path):
    """Executes real two-channel broadcast audio normalization using FFmpeg loudnorm filter."""
    out = tmp_path / "audio_normalized.mp4"
    info = {"duration": 1.0, "width": 320, "height": 240, "audio": True}
    opts = {
        "preset": "landscape",
        "format": "mp4",
        "normalize_audio": True,
    }
    cmd = video.build_command(ffmpeg_bin, sample_video, out, info, opts, None)
    res = subprocess.run(cmd, cwd=sample_video.parent, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"FFmpeg loudnorm failed: {res.stderr}"
    assert out.is_file()
    assert out.stat().st_size > 1000


def test_real_ffmpeg_subtitles_captions(ffmpeg_bin: str, sample_video: Path, tmp_path: Path):
    """Tests burning real subtitles/captions into a video stream using FFmpeg."""
    srt = tmp_path / "test_subs.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nSTARK AI Production Verification\n\n",
        encoding="utf-8",
    )
    out = tmp_path / "subtitled.mp4"
    info = {"duration": 1.0, "width": 320, "height": 240, "audio": True}
    opts = {
        "preset": "landscape",
        "format": "mp4",
        "captions": "burn",
    }
    cmd = video.build_command(ffmpeg_bin, sample_video, out, info, opts, srt.name)
    res = subprocess.run(cmd, cwd=sample_video.parent, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"FFmpeg subtitles burn-in failed: {res.stderr}"
    assert out.is_file()
    assert out.stat().st_size > 1000
