"""Decode local input to bounded 16 kHz mono PCM using FFmpeg."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import CounselError


def ffmpeg_executable(configured: str = "") -> str:
    if configured:
        found = shutil.which(configured)
        if found:
            return found
        raise CounselError("The configured FFmpeg executable does not exist.")
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def normalize_audio(source: Path, target: Path, ffmpeg: str = "") -> None:
    if not source.is_file() or source.stat().st_size == 0:
        raise CounselError("Select a non-empty local audio file.")
    command = [
        ffmpeg_executable(ffmpeg),
        "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-protocol_whitelist", "file,pipe",
        "-i", str(source.resolve()),
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(target),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CounselError("FFmpeg could not decode the recording within 10 minutes.") from exc
    if result.returncode or not target.exists() or target.stat().st_size <= 44:
        raise CounselError("FFmpeg rejected the recording. Check that it contains a supported audio stream.")
