"""ffmpeg helpers: audio as .m4a, and a strip of frames so a persona can see a video."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v"})


class FFmpegError(RuntimeError):
    """ffmpeg or ffprobe is missing."""


def to_m4a(source: Path, folder: Path) -> Path:
    """The audio of a voice note or video as AAC in an .m4a, written into ``folder``."""
    target = folder / f"{source.stem}.m4a"
    _ffmpeg("-i", str(source), "-vn", "-c:a", "aac", "-b:a", "64k", str(target))
    return target


def frame_strip(video: Path, *, frames: int = 6, columns: int = 3, width: int = 360) -> Path:
    """Evenly spaced frames of a video, tiled into one JPEG next to it."""
    rows = -(-frames // columns)
    rate = frames / max(_duration(video), 0.1)
    target = video.with_name(f"{video.stem}-frames.jpg")
    tiles = f"fps={rate:.6f},scale={width}:-2,tile={columns}x{rows}"
    _ffmpeg("-i", str(video), "-vf", tiles, "-frames:v", "1", "-q:v", "3", str(target))
    return target


def has_audio(video: Path) -> bool:
    """Whether a video has a sound track at all."""
    return bool(_ffprobe(video, "-select_streams", "a", "-show_entries", "stream=index"))


def _duration(video: Path) -> float:
    return float(_ffprobe(video, "-show_entries", "format=duration") or 0)


def _ffmpeg(*args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed tool, our own file paths
        [_tool("ffmpeg"), "-y", "-loglevel", "error", *args], check=True, capture_output=True
    )


def _ffprobe(video: Path, *args: str) -> str:
    plain = ["-of", "default=noprint_wrappers=1:nokey=1"]
    argv = [_tool("ffprobe"), "-v", "error", *args, *plain, str(video)]
    done = subprocess.run(argv, check=True, capture_output=True, text=True)  # noqa: S603
    return done.stdout.strip()


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        msg = f"{name} is not installed"
        raise FFmpegError(msg)
    return path
