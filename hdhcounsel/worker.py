"""One isolated, network-denied inference job per process."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import CounselError, Settings
from .privacy import configure_offline, deny_network
from .storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        config = request["settings"]
        config["models_dir"] = Path(config["models_dir"])
        config["output_dir"] = Path(config["output_dir"])
        settings = Settings(**config)
        settings.validate_assets()
        configure_offline(settings.models_dir)
        deny_network()
        from .pipeline import transcribe_normalized

        transcript = transcribe_normalized(Path(request["audio"]), settings)
        write_json(args.result, {"ok": True, "transcript": transcript.to_dict()})
        return 0
    except Exception as exc:
        # Do not propagate exception strings from ML libraries: they can contain
        # input paths, model tokens, or transcript fragments.
        message = str(exc) if isinstance(exc, CounselError) else (
            f"Local speech processing failed ({type(exc).__name__}). "
            "Check model preparation, native CUDA/FFmpeg libraries, and available VRAM. "
            "Try batch_size=1 or CPU/int8."
        )
        write_json(args.result, {"ok": False, "error": message, "error_type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
