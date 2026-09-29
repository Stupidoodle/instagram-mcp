"""ffmpeg helpers, run against tiny generated videos."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from instagram_mcp.video import FFmpegError, frame_strip, has_audio, to_m4a

if TYPE_CHECKING:
    from pathlib import Path

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _video(folder: Path, name: str, *, sound: bool, seconds: float = 2.0) -> Path:
    path = folder / name
    argv = ["ffmpeg", "-y", "-loglevel", "error"]
    argv += ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=240x426:rate=15"]
    if sound:
        argv += ["-f", "lavfi", "-i", f"sine=duration={seconds}", "-shortest", "-c:a", "aac"]
    argv += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(argv, check=True)  # noqa: S603 - test fixture
    return path


def _size(image: Path) -> str:
    argv = ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0"]
    done = subprocess.run([*argv, str(image)], check=True, capture_output=True, text=True)  # noqa: S603
    return done.stdout


@needs_ffmpeg
def test_frame_strip_tiles_six_frames(tmp_path: Path) -> None:
    strip = frame_strip(_video(tmp_path, "reel.mp4", sound=False))
    assert strip == tmp_path / "reel-frames.jpg"
    assert _size(strip).strip() == "1080,1280"  # 3x2 tiles, each 360 wide


@needs_ffmpeg
def test_audio_is_found_and_extracted(tmp_path: Path) -> None:
    loud = _video(tmp_path, "loud.mp4", sound=True)
    assert has_audio(loud)
    assert not has_audio(_video(tmp_path, "mute.mp4", sound=False))
    audio = to_m4a(loud, tmp_path)
    assert audio == tmp_path / "loud.m4a"
    assert audio.stat().st_size > 0


def test_missing_ffmpeg_is_a_clear_error(tmp_path: Path) -> None:
    with (
        patch("instagram_mcp.video.shutil.which", return_value=None),
        pytest.raises(FFmpegError, match="ffmpeg is not installed"),
    ):
        to_m4a(tmp_path / "v.ogg", tmp_path)
