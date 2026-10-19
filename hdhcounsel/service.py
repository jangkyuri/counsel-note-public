"""Application boundary: normalize audio, isolate inference, return typed evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from .audio import normalize_audio
from .config import CounselError, Settings
from .domain import Transcript
from .storage import write_json


def transcribe(source: Path, settings: Settings) -> Transcript:
    settings.validate_assets()
    with tempfile.TemporaryDirectory(prefix="hdhcounsel-") as directory:
        work = Path(directory)
        audio = work / "input.wav"
        normalize_audio(source, audio, settings.ffmpeg)
        config = asdict(settings)
        config["models_dir"] = str(settings.models_dir)
        config["output_dir"] = str(settings.output_dir)
        request, result = work / "request.json", work / "result.json"
        write_json(request, {"audio": str(audio), "settings": config})
        environment = os.environ.copy()
        # Credentials are only relevant to explicit online preparation.
        for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            environment.pop(name, None)
        try:
            completed = subprocess.run(
                [settings.worker_python or sys.executable, "-I", "-m", "hdhcounsel.worker",
                 str(request), str(result)],
                cwd=work,
                env=environment,
                capture_output=True,
                timeout=settings.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CounselError("The local transcription time limit was exceeded. No result was saved.") from exc
        except OSError as exc:
            raise CounselError("The isolated transcription worker could not start.") from exc
        if not result.is_file():
            raise CounselError(
                "The speech worker exited without a result. Confirm HDHcounsel is installed in this "
                "Python environment and the native runtime is available."
            )
        payload = json.loads(result.read_text(encoding="utf-8"))
        if completed.returncode != 0 or not payload["ok"]:
            raise CounselError(payload.get("error", "The speech worker failed."))
        return Transcript.from_dict(payload["transcript"])
