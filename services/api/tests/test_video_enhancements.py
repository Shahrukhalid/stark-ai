from __future__ import annotations

from pathlib import Path
from stark import video


def test_build_command_audio_normalization(tmp_path: Path):
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    info = {"duration": 60.0, "width": 1920, "height": 1080, "audio": True}

    cmd = video.build_command(
        "ffmpeg",
        src,
        out,
        info,
        {"preset": "vertical", "format": "mp4", "normalize_audio": True},
        None,
    )
    assert "-af" in cmd
    af = cmd[cmd.index("-af") + 1]
    assert "loudnorm=I=-14:LRA=11:TP=-1.5" in af


def test_build_command_color_grading(tmp_path: Path):
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    info = {"duration": 60.0, "width": 1920, "height": 1080, "audio": True}

    cmd = video.build_command(
        "ffmpeg",
        src,
        out,
        info,
        {
            "preset": "landscape",
            "format": "mp4",
            "brightness": 0.1,
            "contrast": 1.2,
            "saturation": 1.3,
            "gamma": 1.1,
        },
        None,
    )
    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "eq=contrast=1.20:brightness=0.10:saturation=1.30:gamma=1.10" in vf


def test_build_command_watermark(tmp_path: Path):
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    info = {"duration": 60.0, "width": 1920, "height": 1080, "audio": True}

    cmd = video.build_command(
        "ffmpeg",
        src,
        out,
        info,
        {
            "preset": "vertical",
            "format": "mp4",
            "watermark_text": "STARK STUDIO",
            "watermark_position": "bottom-right",
            "watermark_size": 28,
            "watermark_color": "yellow",
        },
        None,
    )
    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "drawtext=text='STARK STUDIO':fontsize=28:fontcolor=0xFFFF00" in vf
    assert "x=w-tw-24:y=h-th-24" in vf


def test_build_command_watermark_injection_hardening(tmp_path: Path):
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    info = {"duration": 60.0, "width": 1920, "height": 1080, "audio": True}

    # Malicious text with filter injection attempt
    cmd = video.build_command(
        "ffmpeg",
        src,
        out,
        info,
        {
            "preset": "vertical",
            "format": "mp4",
            "watermark_text": "Safe'':scale=100:100;[0:v]evil%[out]",
            "watermark_position": "top-left",
        },
        None,
    )
    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "drawtext=" in vf
    # Metacharacters like single quotes, colons, percent signs and brackets are sanitized
    assert "evil" in vf
    assert ":" not in vf.split("text='")[1].split("':")[0]


def test_build_command_image_watermark(tmp_path: Path):
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    info = {"duration": 60.0, "width": 1920, "height": 1080, "audio": True}

    logo = tmp_path / "logo.png"
    logo.write_bytes(b"dummy_logo")

    cmd = video.build_command(
        "ffmpeg",
        src,
        out,
        info,
        {
            "preset": "vertical",
            "format": "mp4",
            "watermark_image": str(logo),
            "watermark_position": "bottom-right",
            "watermark_opacity": 0.8,
            "watermark_scale": 0.25,
        },
        None,
    )
    assert "-filter_complex" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "colorchannelmixer=aa=0.80" in fc
    assert "scale=iw*0.25:-1" in fc
    assert "overlay=W-w-24:H-h-24" in fc



def test_srt_import_export_helpers(tmp_path: Path, settings):
    d = tmp_path / "media" / "video"
    d.mkdir(parents=True, exist_ok=True)
    settings.workspace_roots = [tmp_path]

    # Create dummy video
    v = d / "test_clip.mp4"
    v.write_bytes(b"dummy")

    srt_content = "1\n00:00:00,000 --> 00:00:02,000\nWelcome to STARK AI\n"
    saved_name = video.import_srt(settings, "test_clip.mp4", srt_content)
    assert saved_name == "test_clip.srt"

    exported = video.export_srt(settings, "test_clip.mp4")
    assert "Welcome to STARK AI" in exported
