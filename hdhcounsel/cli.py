"""Small explicit CLI: online prepare, offline transcribe, local UI."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

from .config import CounselError, Settings


def main() -> int:
    parser = argparse.ArgumentParser(prog="hdhcounsel")
    parser.add_argument("--config", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="ONLINE: download models, never process audio")
    prepare.add_argument("--no-diarization", action="store_true")
    run = commands.add_parser("transcribe", help="OFFLINE: transcribe a local recording")
    run.add_argument("audio", type=Path)
    run.add_argument("--student", default="")
    run.add_argument("--date", type=date.fromisoformat, default=date.today())
    run.add_argument("--no-diarization", action="store_true")
    run.add_argument("--cpu", action="store_true")
    commands.add_parser("doctor", help="Check local configuration and assets without loading models")
    ui = commands.add_parser("ui", help="Open local review UI on loopback only")
    ui.add_argument("--port", type=int, default=8517)
    args = parser.parse_args()
    try:
        settings = Settings.load(args.config)
        if getattr(args, "no_diarization", False):
            settings = settings.without_diarization()
        if getattr(args, "cpu", False):
            settings = replace(settings, device="cpu", compute_type="int8", batch_size=1)
        if args.command == "prepare":
            from .prepare import prepare_models

            print(json.dumps(prepare_models(settings), indent=2))
        elif args.command == "doctor":
            from .audio import ffmpeg_executable

            print(f"FFmpeg: {ffmpeg_executable(settings.ffmpeg)}")
            settings.validate_assets()
            print("Local assets present. Inference compatibility still requires an audio smoke test.")
        elif args.command == "transcribe":
            from .service import transcribe
            from .storage import Record

            transcript = transcribe(args.audio, settings)
            record = Record.create(settings.output_dir, args.student, args.date.isoformat(), transcript)
            print(record.path)
            print(f"Segments: {len(transcript.segments)}; diarized: {transcript.diarized}")
        elif args.command == "ui":
            import os

            environment = os.environ.copy()
            if args.config:
                environment["HDHCOUNSEL_CONFIG"] = str(args.config.resolve())
            return subprocess.call([
                sys.executable, "-I", "-m", "streamlit", "run",
                str(Path(__file__).with_name("ui.py")),
                "--server.address", "127.0.0.1",
                "--server.port", str(args.port),
                "--browser.gatherUsageStats", "false",
            ], env=environment)
        return 0
    except (CounselError, OSError, ValueError) as exc:
        print(f"HDHcounsel: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
