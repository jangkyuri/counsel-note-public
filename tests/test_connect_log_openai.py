from pathlib import Path

import pytest
import requests

from counsel_modules import openai_processing
from counsel_modules.openai_processing import OpenAIProcessingError, summarize, transcribe


def response(status: int, payload: bytes) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = payload
    return result


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-placeholder")
    monkeypatch.delenv("CONNECT_LOG_OPENAI_TRANSCRIPTION_MODEL", raising=False)
    monkeypatch.delenv("CONNECT_LOG_OPENAI_SUMMARY_MODEL", raising=False)


def test_transcription_sends_audio_without_original_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clip = tmp_path / "fixture-private-name.m4a"
    clip.write_bytes(b"fake audio")

    def post(url: str, **kwargs: dict) -> requests.Response:
        assert url == "https://api.openai.com/v1/audio/transcriptions"
        name, file = kwargs["files"]["file"]
        assert name == "recording.m4a"
        assert file.read() == b"fake audio"
        assert kwargs["data"]["model"] == "gpt-transcribe"
        assert kwargs["data"]["language"] == "ko"
        assert kwargs["allow_redirects"] is False
        return response(200, b'{"text":"synthetic transcript"}')

    monkeypatch.setattr(requests, "post", post)
    result = transcribe(clip)
    assert result.text == "synthetic transcript"
    assert not result.segments


def test_preserves_real_speaker_segments_when_model_provides_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clip = tmp_path / "synthetic.wav"
    clip.write_bytes(b"fake audio")
    monkeypatch.setenv("CONNECT_LOG_OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe-diarize")

    def post(url: str, **kwargs: dict) -> requests.Response:
        assert kwargs["data"]["response_format"] == "diarized_json"
        assert kwargs["data"]["chunking_strategy"] == "auto"
        return response(200, b'{"text":"Example","segments":[{"start":0,"end":1,"speaker":"A","text":"Example"}]}')

    monkeypatch.setattr(requests, "post", post)
    segment, = transcribe(clip).segments
    assert (segment.start, segment.end, segment.speaker, segment.text) == (0, 1, "A", "Example")


def test_oversize_audio_rejected_without_ffmpeg_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clip = tmp_path / "oversize.wav"
    clip.write_bytes(b"fake audio")
    monkeypatch.setattr(openai_processing, "MAX_AUDIO_BYTES", 1)
    monkeypatch.setattr(openai_processing.shutil, "which", lambda name: None)
    monkeypatch.setattr(requests, "post", lambda *a, **kw: pytest.fail("unexpected upload"))
    with pytest.raises(OpenAIProcessingError, match="FFmpeg"):
        transcribe(clip)
    assert clip.read_bytes() == b"fake audio"


def test_summary_reads_only_text_and_disables_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    def post(url: str, **kwargs: dict) -> requests.Response:
        assert url == "https://api.openai.com/v1/responses"
        assert kwargs["json"]["store"] is False
        assert kwargs["json"]["tools"] == []
        assert kwargs["json"]["input"] == "Synthetic recording transcript"
        return response(
            200, b'{"status":"completed","output":[{"type":"reasoning"},'
            b'{"type":"message","content":[{"type":"output_text","text":"synthetic summary"}]}]}',
        )

    monkeypatch.setattr(requests, "post", post)
    assert summarize("Synthetic recording transcript", instructions="summarize") == "synthetic summary"


def test_no_key_stops_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setattr(requests, "post", lambda *a, **kw: pytest.fail("unexpected network request"))
    with pytest.raises(OpenAIProcessingError, match="OPENAI_API_KEY"):
        summarize("test", instructions="test")


@pytest.mark.parametrize("status", [401, 404, 429, 503])
def test_api_errors_are_safe_and_pause_batch(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(requests, "post", lambda *a, **kw: response(status, b"PRIVATE UPSTREAM TEXT"))
    with pytest.raises(OpenAIProcessingError) as exc:
        summarize("test", instructions="test")
    assert exc.value.pause_batch
    assert "PRIVATE UPSTREAM TEXT" not in str(exc.value)
    assert "test-only-placeholder" not in str(exc.value)


def test_incomplete_response_is_not_returned_as_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        requests, "post",
        lambda *a, **kw: response(200, b'{"status":"incomplete","output":[{"type":"message","content":[{"type":"output_text","text":"Partial"}]}]}'),
    )
    with pytest.raises(OpenAIProcessingError):
        summarize("test", instructions="test")
