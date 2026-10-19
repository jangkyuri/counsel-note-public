"""Validated configuration shared by the CLI, worker and review UI."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path


class CounselError(RuntimeError):
    """An actionable application error, safe to show without a traceback."""


@dataclass(frozen=True)
class Settings:
    models_dir: Path
    output_dir: Path
    device: str = "cuda"
    compute_type: str = "float16"
    batch_size: int = 4
    diarize: bool = True
    num_speakers: int | None = None
    ffmpeg: str = ""
    timeout_seconds: int = 14400
    worker_python: str = ""

    def __post_init__(self) -> None:
        if self.device not in {"cpu", "cuda"}:
            raise CounselError("device must be cpu or cuda.")
        allowed = {"cpu": {"int8", "float32"}, "cuda": {"float16", "int8_float16", "float32"}}
        if self.compute_type not in allowed[self.device]:
            raise CounselError(f"Unsupported compute_type for {self.device}: {self.compute_type}")
        if type(self.batch_size) is not int or not 1 <= self.batch_size <= 32:
            raise CounselError("batch_size must be an integer between 1 and 32.")
        if type(self.diarize) is not bool:
            raise CounselError("diarize must be true or false.")
        if self.num_speakers is not None and (
            type(self.num_speakers) is not int or not 1 <= self.num_speakers <= 20
        ):
            raise CounselError("num_speakers must be an integer between 1 and 20, or omitted.")
        if type(self.timeout_seconds) is not int or self.timeout_seconds < 1:
            raise CounselError("timeout_seconds must be a positive integer.")
        if not isinstance(self.ffmpeg, str):
            raise CounselError("ffmpeg must be an executable path string.")
        if not isinstance(self.worker_python, str):
            raise CounselError("worker_python must be an executable path string.")

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        root = Path(os.environ.get("HDHCOUNSEL_HOME", Path.home() / ".hdhcounsel")).expanduser()
        config_path = path or root / "config.toml"
        values: dict = {}
        if config_path.exists():
            try:
                with config_path.open("rb") as handle:
                    values = tomllib.load(handle).get("hdhcounsel", {})
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise CounselError("Cannot read HDHcounsel configuration.") from exc
        if not isinstance(values, dict):
            raise CounselError("The hdhcounsel configuration must be a TOML table.")
        fields = set(cls.__dataclass_fields__)
        unknown = set(values) - fields
        if unknown:
            raise CounselError(f"Unknown configuration fields: {', '.join(sorted(unknown))}")
        try:
            for name, default in (("models_dir", root / "models"), ("output_dir", root / "records")):
                candidate = Path(values.get(name, default)).expanduser()
                values[name] = (
                    candidate if candidate.is_absolute() else config_path.parent / candidate
                ).resolve()
            if values.get("device") == "cpu" and "compute_type" not in values:
                values["compute_type"] = "int8"
            if values.get("worker_python"):
                worker = Path(values["worker_python"]).expanduser()
                values["worker_python"] = str((
                    worker if worker.is_absolute() else config_path.parent / worker
                ).resolve())
            return cls(**values)
        except (TypeError, ValueError) as exc:
            raise CounselError("Configuration values have invalid types. Check hdhcounsel.example.toml.") from exc

    def without_diarization(self) -> Settings:
        return replace(self, diarize=False, num_speakers=None)

    def validate_assets(self) -> None:
        required = [
            self.models_dir / "asr" / "model.bin",
            self.models_dir / "asr" / "config.json",
            self.models_dir / "asr" / "tokenizer.json",
            self.models_dir / "align-ko" / "config.json",
            self.models_dir / "align-ko" / "model.safetensors",
            self.models_dir / "align-ko" / "vocab.json",
            self.models_dir / "nltk_data" / "tokenizers" / "punkt_tab",
        ]
        if self.diarize:
            required.append(self.models_dir / "diarization" / "config.yaml")
        if any(not path.exists() for path in required):
            raise CounselError(
                "Local model assets are missing. Run `hdhcounsel prepare` before opening private audio. "
                "Community-1 requires accepting its model terms and a read-only HF token. "
                "Use --no-diarization only when speaker labels are not needed."
            )
