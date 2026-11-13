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
    """Executes real audio normalization, comparing baseline vs processed media, and distinguishing single-pass from genuine two-pass normalization."""
    # 0. Measure baseline media audio loudness
    baseline_metrics = measure_audio_loudness(ffmpeg_bin, sample_video)
    base_i = float(baseline_metrics["input_i"])
    base_tp = float(baseline_metrics["input_tp"])

    # 1. Test Single-Pass dynamic normalization
    out_single = tmp_path / "audio_single_pass.mp4"
    info = {"duration": 1.0, "width": 320, "height": 240, "audio": True}
    opts_single = {
        "preset": "landscape",
        "format": "mp4",
        "normalize_audio": True,
    }
    cmd_single = video.build_command(ffmpeg_bin, sample_video, out_single, info, opts_single, None)
    res_single = subprocess.run(cmd_single, cwd=sample_video.parent, capture_output=True, text=True, check=False)
    assert res_single.returncode == 0, f"Single-pass FFmpeg loudnorm failed: {res_single.stderr}"
    assert out_single.is_file()

    probe_single = probe_media(ffprobe_bin, out_single)
    a_single = next(s for s in probe_single["streams"] if s["codec_type"] == "audio")
    assert a_single["codec_name"] == "aac"

    single_metrics = measure_audio_loudness(ffmpeg_bin, out_single)
    single_i = float(single_metrics["input_i"])
    single_tp = float(single_metrics["input_tp"])
    assert -17.0 <= single_i <= -12.0, f"Single-pass loudness {single_i} LUFS outside window [-17, -12]"
    assert single_tp <= -1.0, f"Single-pass true peak {single_tp} dB exceeds ceiling"
    verify_decoding_integrity(ffmpeg_bin, out_single)

    # 2. Test Genuine Two-Pass linear normalization
    # Pass 1: Parse measured statistics from baseline
    pass1_stats = {
        "input_i": baseline_metrics["input_i"],
        "input_lra": baseline_metrics["input_lra"],
        "input_tp": baseline_metrics["input_tp"],
        "input_thresh": baseline_metrics["input_thresh"],
        "target_offset": baseline_metrics["target_offset"],
    }

    # Pass 2: Apply linear normalization using Pass 1 measurements
    out_twopass = tmp_path / "audio_two_pass.mp4"
    opts_twopass = {
        "preset": "landscape",
        "format": "mp4",
        "normalize_audio": True,
        "loudnorm_params": pass1_stats,
    }
    cmd_twopass = video.build_command(ffmpeg_bin, sample_video, out_twopass, info, opts_twopass, None)
    assert any("linear=true" in arg for arg in cmd_twopass), "Two-pass command must configure linear=true filter"
    assert any(f"measured_I={pass1_stats['input_i']}" in arg for arg in cmd_twopass)

    res_twopass = subprocess.run(cmd_twopass, cwd=sample_video.parent, capture_output=True, text=True, check=False)
    assert res_twopass.returncode == 0, f"Two-pass FFmpeg loudnorm failed: {res_twopass.stderr}"
    assert out_twopass.is_file()

    twopass_metrics = measure_audio_loudness(ffmpeg_bin, out_twopass)
    twopass_i = float(twopass_metrics["input_i"])
    twopass_tp = float(twopass_metrics["input_tp"])
    assert -16.0 <= twopass_i <= -12.0, f"Two-pass loudness {twopass_i} LUFS outside target window"
    assert twopass_tp <= -1.0
    verify_decoding_integrity(ffmpeg_bin, out_twopass)

    # 3. Baseline comparison and distinguishing single-pass vs genuine two-pass:
    # A) Baseline vs Processed: baseline loudness and bytes differ from normalized output
    assert sample_video.read_bytes() != out_single.read_bytes(), "Processed media must differ from baseline media"
    assert abs(base_i - (-14.0)) > 1.5 or base_tp != single_tp, "Baseline audio metrics must differ from normalized target"

    # B) Distinguish single-pass dynamic from genuine two-pass linear normalization
    single_filter = next(arg for arg in cmd_single if "loudnorm" in arg)
    twopass_filter = next(arg for arg in cmd_twopass if "loudnorm" in arg)
    assert "linear=true" not in single_filter, "Single-pass must not use linear mode"
    assert "linear=true" in twopass_filter, "Genuine two-pass must configure linear=true"
    for param in ("measured_I", "measured_LRA", "measured_TP", "measured_thresh", "offset"):
        assert f"{param}=" in twopass_filter, f"Two-pass filter must include {param}"
        assert f"{param}=" not in single_filter, f"Single-pass filter must not include {param}"


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


@pytest.mark.asyncio
async def test_video_studio_automatic_two_pass_loudnorm(ffmpeg_bin: str, ffprobe_bin: str, sample_video: Path, tmp_path: Path):
    """Verifies that selecting normalize_audio in VideoStudio automatically conducts a two-pass loudnorm analysis and validates output loudness."""
    import asyncio
    import shutil
    import time
    from stark.config import Settings
    from stark.video import VideoStudio

    data_dir = tmp_path / "studio_data"
    media_dir = data_dir / "media" / "video"
    media_dir.mkdir(parents=True, exist_ok=True)
    in_video = media_dir / "input_clip.mp4"
    shutil.copy(sample_video, in_video)

    settings = Settings(data_dir=data_dir, workspace_roots=[data_dir])
    studio = VideoStudio(settings)

    opts = {
        "file": in_video.name,
        "preset": "landscape",
        "format": "mp4",
        "normalize_audio": True,
    }
    job = studio.start(opts)
    job_id = job["id"]

    # Wait for background job to finish
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        cur_job = studio.jobs.get(job_id)
        if cur_job and cur_job.get("status") in ("done", "failed", "cancelled"):
            break
        await asyncio.sleep(0.2)

    cur_job = studio.jobs[job_id]
    assert cur_job["status"] == "done", f"Video export failed: {cur_job.get('error')}"
    out_file = media_dir / cur_job["output"]
    assert out_file.is_file() and out_file.stat().st_size > 0

    # Validate measured loudness attached to job
    assert "measured_loudness" in cur_job, "measured_loudness was not recorded on completed job"
    assert cur_job.get("audio_normalization") == "two_pass", f"Expected two_pass, got {cur_job.get('audio_normalization')}"
    metrics = cur_job["measured_loudness"]
    lufs = metrics["integrated_lufs"]
    tp = metrics["true_peak_db"]
    assert -16.0 <= lufs <= -12.0, f"Automatic 2-pass loudness {lufs} LUFS is out of spec"
    assert tp <= -1.0, f"True peak {tp} dB exceeds ceiling"

    # Verify decoding integrity
    verify_decoding_integrity(ffmpeg_bin, out_file)


@pytest.mark.asyncio
async def test_video_studio_merge_two_pass_loudnorm(ffmpeg_bin: str, ffprobe_bin: str, sample_video: Path, tmp_path: Path):
    """Verifies that multi-video merge workflows execute two-pass loudness normalization across joined streams."""
    import asyncio
    import shutil
    import time
    from stark.config import Settings
    from stark.video import VideoStudio

    data_dir = tmp_path / "merge_studio_data"
    media_dir = data_dir / "media" / "video"
    media_dir.mkdir(parents=True, exist_ok=True)
    v1 = media_dir / "clip_1.mp4"
    v2 = media_dir / "clip_2.mp4"
    shutil.copy(sample_video, v1)
    shutil.copy(sample_video, v2)

    settings = Settings(data_dir=data_dir, workspace_roots=[data_dir])
    studio = VideoStudio(settings)

    opts = {
        "files": [v1.name, v2.name],
        "preset": "landscape",
        "format": "mp4",
        "normalize_audio": True,
        "transition": 0.0,
    }
    job = studio.start(opts)
    job_id = job["id"]

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        cur_job = studio.jobs.get(job_id)
        if cur_job and cur_job.get("status") in ("done", "failed", "cancelled"):
            break
        await asyncio.sleep(0.2)

    cur_job = studio.jobs[job_id]
    assert cur_job["status"] == "done", f"Merge failed: {cur_job.get('error')}"
    out_file = media_dir / cur_job["output"]
    assert out_file.is_file() and out_file.stat().st_size > 0

    assert cur_job.get("audio_normalization") == "two_pass", f"Expected two_pass, got {cur_job.get('audio_normalization')}"
    assert "measured_loudness" in cur_job
    metrics = cur_job["measured_loudness"]
    lufs = metrics["integrated_lufs"]
    assert -16.0 <= lufs <= -12.0, f"Merged 2-pass loudness {lufs} LUFS is out of spec"
    verify_decoding_integrity(ffmpeg_bin, out_file)


@pytest.mark.asyncio
async def test_video_studio_audio_normalization_fallbacks(ffmpeg_bin: str, tmp_path: Path):
    """Verifies that videos without audio skip normalization cleanly, and strict two-pass fails with explicit status."""
    import asyncio
    import time
    from stark.config import Settings
    from stark.video import VideoStudio

    data_dir = tmp_path / "fallback_studio_data"
    media_dir = data_dir / "media" / "video"
    media_dir.mkdir(parents=True, exist_ok=True)

    # Generate video without audio
    silent_vid = media_dir / "silent.mp4"
    cmd = [
        ffmpeg_bin, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=24",
        "-c:v", "libx264", "-preset", "ultrafast", "-an", "-pix_fmt", "yuv420p", str(silent_vid),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    settings = Settings(data_dir=data_dir, workspace_roots=[data_dir])
    studio = VideoStudio(settings)

    opts = {
        "file": silent_vid.name,
        "preset": "landscape",
        "format": "mp4",
        "normalize_audio": True,
    }
    job = studio.start(opts)
    job_id = job["id"]

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        cur_job = studio.jobs.get(job_id)
        if cur_job and cur_job.get("status") in ("done", "failed", "cancelled"):
            break
        await asyncio.sleep(0.2)

    cur_job = studio.jobs[job_id]
    assert cur_job["status"] == "done"
    assert cur_job.get("audio_normalization") == "skipped_no_audio"



