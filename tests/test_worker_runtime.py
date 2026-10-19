from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from hdhcounsel import service
from hdhcounsel.config import CounselError, Settings
from hdhcounsel.domain import Segment, Transcript
from hdhcounsel.storage import write_json


def test_config_resolves_worker_python_relative_to_config_file(tmp_path: Path) -> None:
    # Given: a private configuration pointing to a separate speech environment.
    path = tmp_path / "config.toml"
    path.write_text(
        '[hdhcounsel]\nworker_python = "runtime/python.exe"\n', encoding="utf-8",
    )

    # When: configuration is loaded from a different working directory.
    settings = Settings.load(path)

    # Then: the worker path belongs to the config directory, not the shell.
    assert settings.worker_python == str((tmp_path / "runtime/python.exe").resolve())


def test_config_rejects_invalid_worker_python_type(tmp_path: Path) -> None:
    # Given: an invalid worker setting at the configuration boundary.
    path = tmp_path / "config.toml"
    path.write_text('[hdhcounsel]\nworker_python = false\n', encoding="utf-8")

    # When / Then: invalid settings fail before any process is launched.
    with pytest.raises(CounselError):
        Settings.load(path)


def test_service_uses_configured_runtime_and_preserves_parent(tmp_path: Path, monkeypatch) -> None:
    # Given: a distinct speech interpreter and no external service or real audio.
    runtime = tmp_path / "speech-python.exe"
    runtime.write_bytes(b"synthetic-placeholder-not-executed")
    settings = replace(Settings(tmp_path, tmp_path), worker_python=str(runtime))
    expected = Transcript((Segment(0, 1, "synthetic"),), 1)
    monkeypatch.setattr(Settings, "validate_assets", lambda self: None)
    monkeypatch.setattr(service, "normalize_audio", lambda *args: None)
    launched = []

    def run(command, **kwargs):
        launched.append(command)
        request = json.loads(Path(command[-2]).read_text(encoding="utf-8"))
        assert request["settings"]["worker_python"] == str(runtime)
        write_json(Path(command[-1]), {"ok": True, "transcript": expected.to_dict()})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(service.subprocess, "run", run)

    # When: the legacy application's service requests local transcription.
    result = service.transcribe(tmp_path / "synthetic.wav", settings)

    # Then: only the worker uses the explicit interpreter; its isolation remains.
    assert result == expected
    assert launched[0][0] == str(runtime)
    assert launched[0][0] != sys.executable
    assert launched[0][1:4] == ["-I", "-m", "hdhcounsel.worker"]
