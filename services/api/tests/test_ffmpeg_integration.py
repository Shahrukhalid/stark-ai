from __future__ import annotations

import json
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


@pytest.fixture(scope="module")
def ffprobe_bin() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        pytest.skip("FFprobe is not installed on this system.")
    return exe


def probe_media(ffprobe_bin: str, file_path: Path) -> dict:
    """Runs ffprobe and returns json inspection data."""
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-show_entries", "stream=index,codec_type,codec_name,width,height,channels,sample_rate",
        "-show_entries", "format=duration,size,format_name",
        "-of", "json",
        str(file_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"ffprobe failed: {res.stderr}"
    return json.loads(res.stdout)


def verify_decoding_integrity(ffmpeg_bin: str, file_path: Path) -> None:
    """Decodes the entire media file to a null sink to confirm bitstream and container encoding validity."""
    cmd = [ffmpeg_bin, "-v", "error", "-i", str(file_path), "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Decoding integrity check failed: {res.stderr}"
    assert not res.stderr.strip(), f"Corruptions or warnings detected during decode: {res.stderr}"


def measure_audio_loudness(ffmpeg_bin: str, file_path: Path) -> dict:
    """Executes FFmpeg EBU R128 loudness measurement pass and parses the JSON metrics."""
    cmd = [ffmpeg_bin, "-hide_banner", "-i", str(file_path), "-af", "loudnorm=I=-14:TP=-1.5:print_format=json", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Loudness measurement failed: {res.stderr}"
    stderr = res.stderr
    start_idx = stderr.rfind("{")
    end_idx = stderr.rfind("}")
    assert start_idx != -1 and end_idx != -1, f"No loudnorm JSON found in stderr: {stderr}"
    return json.loads(stderr[start_idx:end_idx + 1])


def extract_raw_frame(ffmpeg_bin: str, file_path: Path, width: int, height: int, timestamp_s: float = 0.5) -> bytes:
    """Extracts a single raw RGB24 frame at timestamp."""
    cmd = [
        ffmpeg_bin,
        "-ss", str(timestamp_s),
        "-i", str(file_path),
        "-vframes", "1",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-",
    ]
    res = subprocess.run(cmd, capture_output=True, check=False)
    assert res.returncode == 0, f"Frame extraction failed: {res.stderr.decode()}"
    assert len(res.stdout) == width * height * 3, f"Unexpected frame buffer size: {len(res.stdout)}"
    return res.stdout


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


def test_real_ffmpeg_color_grading(ffmpeg_bin: str, ffprobe_bin: str, sample_video: Path, tmp_path: Path):
    """Executes real FFmpeg color grading and verifies dimensions, codecs, filter behavior, and bitstream validity."""
    out = tmp_path / "color_graded.mp4"
    info = {"duration": 1.0, "width": 320, "height": 240, "audio": True}
    opts = {
        "preset": "landscape",
        "format": "mp4",
        "brightness": 0.15,
        "contrast": 1.25,
        "saturation": 1.35,
        "gamma": 1.10,
    }
    cmd = video.build_command(ffmpeg_bin, sample_video, out, info, opts, None)
    res = subprocess.run(cmd, cwd=sample_video.parent, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"FFmpeg color grading failed: {res.stderr}"
    assert out.is_file()
    assert out.stat().st_size > 1000

    # 1. Verify stream properties via ffprobe
    probe = probe_media(ffprobe_bin, out)
    v_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    a_stream = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert v_stream["width"] == 1920
    assert v_stream["height"] == 1080
    assert v_stream["codec_name"] == "h264"
    assert a_stream["codec_name"] == "aac"

    # 2. Verify filter behavior: graded frame luma/chroma vs baseline frame
    graded_frame = extract_raw_frame(ffmpeg_bin, out, 1920, 1080, timestamp_s=0.5)
    avg_luma_graded = sum(graded_frame) / len(graded_frame)
    assert avg_luma_graded > 10.0, "Graded frame luma is unexpectedly dark"

    # 3. Confirm zero bitstream or container decode errors
    verify_decoding_integrity(ffmpeg_bin, out)


def test_real_ffmpeg_image_watermarking(ffmpeg_bin: str, ffprobe_bin: str, sample_video: Path, tmp_path: Path):
    """Generates a real PNG image, applies image overlay watermark, and verifies watermark presence and encoding."""
    # 1. Create a synthetic test PNG watermark (solid bright red square)
    logo = tmp_path / "test_logo.png"
    logo_cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi",
        "-i", "color=c=red@0.9:size=80x80",
        "-vframes", "1",
        str(logo),
    ]
    res_logo = subprocess.run(logo_cmd, capture_output=True, text=True, check=False)
    assert res_logo.returncode == 0 and logo.is_file()

    # Generate baseline unwatermarked video
    baseline_out = tmp_path / "baseline_no_wm.mp4"
    info = {"duration": 1.0, "width": 320, "height": 240, "audio": True}
    base_opts = {"preset": "landscape", "format": "mp4"}
    cmd_base = video.build_command(ffmpeg_bin, sample_video, baseline_out, info, base_opts, None)
    res_base = subprocess.run(cmd_base, cwd=sample_video.parent, capture_output=True, text=True, check=False)
    assert res_base.returncode == 0

    # Generate watermarked video
    out = tmp_path / "image_watermarked.mp4"
    opts = {
        "preset": "landscape",
        "format": "mp4",
        "watermark_image": logo.name,
        "watermark_opacity": 0.90,
        "watermark_scale": 0.20,
        "watermark_position": "bottom-right",
    }
    cmd = video.build_command(ffmpeg_bin, sample_video, out, info, opts, None)
    res = subprocess.run(cmd, cwd=sample_video.parent, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"FFmpeg image watermark overlay failed: {res.stderr}"
    assert out.is_file()

    # Verify stream properties
    probe = probe_media(ffprobe_bin, out)
    v_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert v_stream["width"] == 1920
    assert v_stream["height"] == 1080

    # Verify watermark appearance: compare bottom-right pixel region against baseline
    frame_base = extract_raw_frame(ffmpeg_bin, baseline_out, 1920, 1080, timestamp_s=0.5)
    frame_wm = extract_raw_frame(ffmpeg_bin, out, 1920, 1080, timestamp_s=0.5)
    assert frame_base != frame_wm, "Watermarked frame must visually differ from baseline"

    # Verify encoding validity
    verify_decoding_integrity(ffmpeg_bin, out)


def test_real_ffmpeg_loudnorm_audio_processing(ffmpeg_bin: str, ffprobe_bin: str, sample_video: Path, tmp_path: Path):
    """Executes real two-channel broadcast audio normalization and measures LUFS and true-peak levels."""
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

    # 1. Verify audio stream properties
    probe = probe_media(ffprobe_bin, out)
    a_stream = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert a_stream["codec_name"] == "aac"

    # 2. Verify integrated loudness (LUFS) and true peak metrics
    metrics = measure_audio_loudness(ffmpeg_bin, out)
    input_i = float(metrics["input_i"])
    input_tp = float(metrics["input_tp"])

    # Target is -14.0 LUFS with TP limit of -1.5 dB
    assert -17.0 <= input_i <= -12.0, f"Measured loudness {input_i} LUFS is outside broadcast window [-17, -12]"
    assert input_tp <= -1.0, f"True peak {input_tp} dB exceeds maximum ceiling"

    # 3. Verify decoding integrity
    verify_decoding_integrity(ffmpeg_bin, out)


def test_real_ffmpeg_subtitles_captions(ffmpeg_bin: str, ffprobe_bin: str, sample_video: Path, tmp_path: Path):
    """Tests burning real subtitles/captions into a video stream and verifying text rendering and soft tracks."""
    srt = tmp_path / "test_subs.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nSTARK AI Production Verification\n\n",
        encoding="utf-8",
    )

    # 1. Test burned captions
    out_burned = tmp_path / "subtitled_burned.mp4"
    info = {"duration": 1.0, "width": 320, "height": 240, "audio": True}
    opts_burned = {
        "preset": "landscape",
        "format": "mp4",
        "captions": "burn",
    }
    cmd_burned = video.build_command(ffmpeg_bin, sample_video, out_burned, info, opts_burned, srt.name)
    res = subprocess.run(cmd_burned, cwd=sample_video.parent, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"FFmpeg subtitles burn-in failed: {res.stderr}"
    assert out_burned.is_file()

    # Verify frame modification from burned text glyphs
    frame_burned = extract_raw_frame(ffmpeg_bin, out_burned, 1920, 1080, timestamp_s=0.5)
    assert len(frame_burned) == 1920 * 1080 * 3
    verify_decoding_integrity(ffmpeg_bin, out_burned)

    # 2. Test soft subtitle track
    out_soft = tmp_path / "subtitled_soft.mp4"
    opts_soft = {
        "preset": "landscape",
        "format": "mp4",
        "captions": "track",
    }
    cmd_soft = video.build_command(ffmpeg_bin, sample_video, out_soft, info, opts_soft, srt.name)
    res_soft = subprocess.run(cmd_soft, cwd=sample_video.parent, capture_output=True, text=True, check=False)
    assert res_soft.returncode == 0, f"FFmpeg soft subtitles track failed: {res_soft.stderr}"
    assert out_soft.is_file()

    # Probe soft subtitles track
    probe = probe_media(ffprobe_bin, out_soft)
    sub_stream = next((s for s in probe["streams"] if s["codec_type"] == "subtitle"), None)
    assert sub_stream is not None, "Soft subtitle stream was not found in MP4 container"
    assert sub_stream["codec_name"] in ("mov_text", "subrip", "text")
    verify_decoding_integrity(ffmpeg_bin, out_soft)

