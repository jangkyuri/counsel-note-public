"""Explicit ONLINE asset provisioning; never accepts audio or student data."""

from __future__ import annotations

import json
import os

from .config import CounselError, Settings
from .storage import write_json


def prepare_models(settings: Settings) -> dict[str, str]:
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
    import nltk
    from huggingface_hub import HfApi, snapshot_download

    models = [
        ("asr", "Systran/faster-whisper-large-v3", ["*.json", "model.bin", "vocabulary.*"]),
        ("align-ko", "kresnik/wav2vec2-large-xlsr-korean", ["*.json", "model.safetensors"]),
    ]
    if settings.diarize:
        models.append(("diarization", "pyannote/speaker-diarization-community-1", None))
    # No implicit machine-wide credentials; callers opt in with HF_TOKEN.
    token = os.environ.get("HF_TOKEN") or False
    manifest_path = settings.models_dir / "manifest.json"
    manifest: dict[str, str] = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    for folder, repository, patterns in models:
        print(f"Preparing {repository}", flush=True)
        try:
            revision = HfApi().model_info(repository, token=token).sha
            if not revision:
                raise CounselError("The model repository did not return a revision.")
            snapshot_download(
                repository, revision=revision, local_dir=settings.models_dir / folder,
                allow_patterns=patterns, token=token,
            )
        except Exception as exc:
            raise CounselError(
                f"Could not prepare {repository}. Check network access and, for Community-1, "
                "accept the Hugging Face model terms and set a read-only HF_TOKEN. "
                "Existing downloaded assets were retained."
            ) from exc
        manifest[repository] = revision
        write_json(manifest_path, manifest)
    nltk_dir = settings.models_dir / "nltk_data"
    if not nltk.download("punkt_tab", download_dir=str(nltk_dir), quiet=True, raise_on_error=True):
        raise CounselError("NLTK punkt_tab preparation failed.")
    # Validate the exact loader used by WhisperX, not just a directory name.
    nltk.data.path.insert(0, str(nltk_dir))
    nltk.data.load("tokenizers/punkt_tab/english.pickle")
    settings.validate_assets()
    return manifest
