"""Offline inference policy. Install only inside the dedicated worker."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_offline(models_dir: Path) -> None:
    os.environ.update({
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "PYANNOTE_METRICS_ENABLED": "0",
        "DO_NOT_TRACK": "1",
        "NLTK_DATA": str(models_dir / "nltk_data"),
    })


def deny_network() -> None:
    def audit(event: str, args: tuple) -> None:
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            raise PermissionError("Network access is disabled in the HDHcounsel inference worker.")

    sys.addaudithook(audit)
