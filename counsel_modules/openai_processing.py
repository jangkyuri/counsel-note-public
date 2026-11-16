from __future__ import annotations

import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final

import requests

API_ROOT: Final = "https://api.openai.com/v1"
MAX_AUDIO_BYTES: Final = 24_000_000
DEFAULT_TRANSCRIPTION_MODEL: Final = "gpt-transcribe"
DIARIZATION_MODEL: Final = "gpt-4o-transcribe-diarize"
DEFAULT_SUMMARY_MODEL: Final = "gpt-5-mini"
AUDIO_SUFFIXES: Final = frozenset({".flac", ".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".ogg", ".wav", ".webm"})


class OpenAIProcessingError(RuntimeError):
    def __init__(self, message: str, *, pause_batch: bool = False) -> None:
        super().__init__(message)
        self.pause_batch = pause_batch


@dataclass(frozen=True, slots=True)
class AudioSegment:
    start: float
    end: float
    speaker: str
    text: str


@dataclass(frozen=True, slots=True)
class Transcription:
    text: str
    segments: tuple[AudioSegment, ...] = ()


def openai_api_enabled() -> bool:
    return os.environ.get("CONNECT_LOG_AI_BACKEND", "").strip().casefold() == "openai"


def require_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise OpenAIProcessingError("OpenAI API 키가 서버에 설정되지 않았습니다 (OPENAI_API_KEY).", pause_batch=True)
    return key


def audio_model() -> str:
    return os.environ.get("CONNECT_LOG_OPENAI_TRANSCRIPTION_MODEL", "").strip() or DEFAULT_TRANSCRIPTION_MODEL


def _result(response: requests.Response) -> dict:
    status = response.status_code
    if status in (401, 403):
        raise OpenAIProcessingError(f"OpenAI API 키 또는 프로젝트 권한을 확인해 주세요 (HTTP {status}).", pause_batch=True)
    if status == 429:
        raise OpenAIProcessingError("OpenAI API 요청 한도 또는 결제 상태를 확인해 주세요 (HTTP 429).", pause_batch=True)
    if status == 404:
        raise OpenAIProcessingError("서버에 설정된 OpenAI 모델을 사용할 수 없습니다 (HTTP 404).", pause_batch=True)
    if status >= 500:
        raise OpenAIProcessingError(f"OpenAI 서비스가 응답하지 않습니다 (HTTP {status}).", pause_batch=True)
    if status != 200:
        raise OpenAIProcessingError(f"OpenAI API가 요청을 처리하지 못했습니다 (HTTP {status}).")
    try:
        data = response.json()
    except ValueError:
        raise OpenAIProcessingError("OpenAI API가 올바른 응답을 반환하지 않았습니다.") from None
    if not isinstance(data, dict):
        raise OpenAIProcessingError("OpenAI API가 올바른 응답을 반환하지 않았습니다.")
    return data


def _compact_audio(source: Path, target: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise OpenAIProcessingError("24 MB를 넘는 녹음은 서버에 FFmpeg가 필요합니다. 파일을 줄여 다시 선택해 주세요.")
    try:
        subprocess.run(
            [
                ffmpeg, "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file",
                "-i", str(source.resolve()), "-vn", "-ac", "1", "-ar", "24000",
                "-c:a", "libmp3lame", "-b:a", "48k", str(target),
            ],
            check=True, timeout=180, capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise OpenAIProcessingError("녹음을 API에 보낼 형식으로 변환하지 못했습니다.") from None
    size = target.stat().st_size
    if not 0 < size <= MAX_AUDIO_BYTES:
        raise OpenAIProcessingError("변환 후에도 녹음이 24 MB를 넘거나 비어 있습니다. 원본을 나눠서 다시 선택해 주세요.")


def transcribe(path: Path) -> Transcription:
    """Send audio only; the original filename and student identifier are not request fields."""
    key = require_api_key()
    suffix = path.suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        raise OpenAIProcessingError("OpenAI API에서 지원하지 않는 녹음 형식입니다.")
    size = path.stat().st_size
    if size == 0:
        raise OpenAIProcessingError("녹음 파일이 비어 있습니다.")
    model = audio_model()
    diarized = model == DIARIZATION_MODEL
    form = {"model": model, "language": "ko", "response_format": "diarized_json" if diarized else "json"}
    if diarized:
        form["chunking_strategy"] = "auto"

    with TemporaryDirectory(prefix="connect-log-openai-") as tmp:
        upload = path
        if size > MAX_AUDIO_BYTES:
            upload = Path(tmp) / "recording.mp3"
            _compact_audio(path, upload)
        with upload.open("rb") as audio:
            try:
                response = requests.post(
                    f"{API_ROOT}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    data=form, files={"file": (f"recording{upload.suffix.lower()}", audio)},
                    timeout=(10, 240), allow_redirects=False,
                )
            except requests.RequestException:
                raise OpenAIProcessingError("OpenAI 전사 요청의 응답을 받지 못했습니다.", pause_batch=True) from None
    data = _result(response)
    raw_text = data.get("text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise OpenAIProcessingError("OpenAI API가 전사 텍스트를 반환하지 않았습니다.")

    segments: list[AudioSegment] = []
    if diarized:
        rows = data.get("segments")
        if not isinstance(rows, list):
            raise OpenAIProcessingError("OpenAI API가 화자 구간을 반환하지 않았습니다.")
        for row in rows:
            if not isinstance(row, dict):
                continue
            start, end = row.get("start"), row.get("end")
            speaker, text = row.get("speaker"), row.get("text")
            if not (
                type(start) in (int, float) and type(end) in (int, float)
                and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end
                and isinstance(speaker, str) and isinstance(text, str) and text.strip()
            ):
                continue
            segments.append(AudioSegment(start, end, speaker, text.strip()))
        if not segments:
            raise OpenAIProcessingError("OpenAI API의 유효한 화자 구간이 없습니다.")
    return Transcription(raw_text.strip(), tuple(segments))


def summarize(prompt: str, *, instructions: str) -> str:
    key = require_api_key()
    model = os.environ.get("CONNECT_LOG_OPENAI_SUMMARY_MODEL", "").strip() or DEFAULT_SUMMARY_MODEL
    payload = {
        "model": model,
        "instructions": instructions,
        "input": prompt,
        "store": False,
        "tools": [],
        "max_output_tokens": 8192,
    }
    try:
        response = requests.post(
            f"{API_ROOT}/responses",
            headers={"Authorization": f"Bearer {key}"},
            json=payload, timeout=(10, 180), allow_redirects=False,
        )
    except requests.RequestException:
        raise OpenAIProcessingError("OpenAI 정리 요청의 응답을 받지 못했습니다.", pause_batch=True) from None
    data = _result(response)
    output = data.get("output")
    if data.get("status") != "completed" or not isinstance(output, list):
        raise OpenAIProcessingError("OpenAI API의 정리 요청이 끝나지 않았습니다.")
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
    summary = "\n".join(parts).strip()
    if not summary:
        raise OpenAIProcessingError("OpenAI API가 정리본을 반환하지 않았습니다.")
    return summary
