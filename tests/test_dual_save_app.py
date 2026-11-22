from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from counsel_modules.audio_batch import (
    create_audio_batch_job, create_audio_batch_state, load_audio_batch_state,
    prepare_audio_batch_retry, save_audio_batch_state,
)
from counsel_modules.dual_save import Lesson, Outcome
from counsel_modules.dual_save_runtime import SaveResult
from scripts.check_csv_worker import load_app

STAMP = "2026-11-22T12:00:00+09:00"
DOC = "qa-synthetic-doc-00001"


def test_prepared_job_uses_existing_save_entrypoint_and_resumes_partial_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    app = load_app(Path(__file__).resolve().parents[1], None)
    monkeypatch.setenv("CONNECT_LOG_DUAL_SAVE_ENABLED", "1")
    audio = tmp_path / "synthetic.wav"
    audio.write_bytes(b"QA synthetic sound")
    monkeypatch.setattr(app, "RECORDING_REPLAY_PATH", tmp_path / "recordings.sqlite3")
    job = create_audio_batch_job(
        audio_path=str(audio), source_name="synthetic.wav", student_name="QA Student",
        author_name="QA", counseling_date=STAMP[:10], processing_mode="qa",
        summary_style="qa", use_vad=False, use_prompt_hint=False,
    )
    job.update(summary="QA-ADD", status="completed")
    state = {"audio_batch_state": create_audio_batch_state([job], STAMP)}
    checkpoint = tmp_path / "checkpoint.json"

    def persist(value) -> None:
        save_audio_batch_state(checkpoint, value)
        state["audio_batch_state"] = value

    monkeypatch.setattr(app, "st", SimpleNamespace(session_state=state))
    monkeypatch.setattr(app, "persist_audio_batch_session_state", persist)
    monkeypatch.setattr(app, "audio_batch_now_iso", lambda: STAMP)
    monkeypatch.setattr(app, "summary_copy_save_allowed", lambda value, *_: bool(value))
    monkeypatch.setattr(app, "admin_notes_export_text_from_summary", lambda **kw: (kw["summary_text"], "QA"))
    monkeypatch.setattr(app, "load_google_docs_map_rows", lambda: [
        {"student_name": "QA Student", "doc_id": DOC, "doc_url": ""},
    ])

    calls: list[Outcome] = []

    def save(name, day, summary, docs_map, previous, on_target) -> SaveResult:
        assert docs_map == [("QA Student", DOC)]
        assert summary == "QA-ADD" and day == date(2026, 11, 22)
        on_target(Lesson("qa", name, day, "qa-subject"), DOC)
        calls.append(previous.admin)
        return SaveResult(
            Outcome.SAVED, Outcome.PENDING if len(calls) == 1 else Outcome.SAVED,
            "qa", DOC, "qa-subject",
        )

    monkeypatch.setattr(app, "save_on_server", save)
    app.save_audio_batch_job_to_admin(job["job_id"])
    restored = load_audio_batch_state(checkpoint)
    assert restored is not None and not app.audio_batch_all_saved(restored)
    assert restored["jobs"][0]["admin_saved"] and not restored["jobs"][0]["docs_saved"]
    retry = prepare_audio_batch_retry(restored, STAMP, require_docs=True)
    assert retry["jobs"][0]["status"] == "pending" and retry["jobs"][0]["summary"] == "QA-ADD"
    state["audio_batch_state"] = retry
    app.save_audio_batch_job_to_admin(job["job_id"])
    assert calls == [Outcome.PENDING, Outcome.EXISTING]
    assert app.audio_batch_all_saved(state["audio_batch_state"])
    # Completion can be acknowledged and the queue replaced with a new upload of the same bytes.
    renamed = tmp_path / "renamed.wav"
    renamed.write_bytes(audio.read_bytes())
    new_job = {**job, "source_name": renamed.name, "audio_path": str(renamed),
               "job_id": "another upload", "summary": "", "status": "pending"}
    state["audio_batch_state"] = create_audio_batch_state([new_job], STAMP)
    assert app.start_next_audio_batch_job()
    replay = state["audio_batch_state"]["jobs"][0]
    assert replay["summary"] == "QA-ADD"
    assert state["current_job_snapshot"]["resume_admin_save_only"] is True
    app.save_audio_batch_job_to_admin(replay["job_id"])
    assert calls == [Outcome.PENDING, Outcome.EXISTING]
    assert replay["admin_saved"] and replay["docs_saved"]

    # A stale concurrent result with different wording must not reach either store.
    divergent = {**new_job, "summary": "second model output", "status": "completed"}
    state["audio_batch_state"] = create_audio_batch_state([divergent], STAMP)
    app.save_audio_batch_job_to_admin(divergent["job_id"])
    assert calls == [Outcome.PENDING, Outcome.EXISTING]
    assert state["audio_batch_state"]["jobs"][0]["status"] == "needs_review"


def test_partial_save_display_does_not_claim_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECT_LOG_DUAL_SAVE_ENABLED", "1")
    view = AppTest.from_string(
        "from counsel_modules.audio_workflow_ui import render_audio_workflow_overview\n"
        "render_audio_workflow_overview({'jobs': [{'counseling_date': '2026-11-22', 'summary': 'QA',"
        " 'status':'needs_review', 'student_name':'QA', 'job_id':'qa', 'admin_sid':'qa',"
        " 'dual_save_sid':'qa', 'admin_saved':True, 'docs_saved':False}]})\n"
    ).run()
    assert not view.exception and not view.success
    assert len(view.warning) == 1
