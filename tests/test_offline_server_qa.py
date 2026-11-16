from __future__ import annotations

import base64
import hashlib
import hmac
import shutil
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from counsel_modules.admin_workspace import (
    ASSERTION_HEADER,
    AdminWorkspace,
    use_worker_workspace,
    verified_workspace,
)
from counsel_modules.atomic_json import write_json_atomic
from counsel_modules.audio_batch import create_audio_batch_job, create_audio_batch_state, save_audio_batch_state
from counsel_modules.log_csv_batch_state import create_log_csv_batch_state, save_log_csv_batch_state
from scripts.check_csv_worker import load_app, transfer_args
from scripts.check_google_docs_parallel import in_flight_request

ROOT = Path(__file__).resolve().parents[1]
QA_SECRET = "synthetic-secret-for-offline-tests-only"


def stamp() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def assertion(user: str) -> str:
    identity = base64.urlsafe_b64encode(user.encode()).decode().rstrip("=")
    message = f"v1.{int(time.time()) + 300}.{identity}"
    return f"{message}.{hmac.new(QA_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()}"


@pytest.fixture
def isolated_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "app"
    students = root / "data" / "students"
    students.mkdir(parents=True)
    for name in ("app.py", "requirements.txt", "APP_VERSION.txt", "BUILD_INFO.json"):
        shutil.copyfile(ROOT / name, root / name)
    for name in ("counsel_modules", "hdhcounsel", "prompts"):
        shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    for filename, text in {
        "google_docs_map.csv": "student_name,doc_id,doc_url\n",
        "google_apps_script_webapp_url.txt": "",
        "admin_api_base_url.txt": "http://127.0.0.1:1",
        "admin_sid_map.csv": "student_name,admin_sid\n",
        "recording_auto_cleanup_enabled.txt": "false\n",
    }.items():
        (students / filename).write_text(text, encoding="utf-8")
    profile = tmp_path / "profile"
    profile.mkdir()
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(profile))
    for key in (
        "OPENAI_API_KEY", "CONNECT_LOG_DOCS_QA_URL", "CONNECT_LOG_DOCS_QA_ID", "CONNECT_LOG_DOCS_QA_BEARER_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CONNECT_LOG_MULTI_USER", "1")
    monkeypatch.setenv("CONNECT_LOG_ADMIN_PROXY_SECRET", QA_SECRET)

    def offline(*args: object, **kwargs: object) -> None:
        raise AssertionError("External connections are disabled in offline server QA")

    monkeypatch.setattr(socket.socket, "connect", offline)
    monkeypatch.setattr(socket.socket, "connect_ex", offline)
    return root


def seed_user(root: Path, user: str, count: int) -> str:
    auth = assertion(user)
    workspace = verified_workspace(root, [auth], QA_SECRET)
    now = stamp()
    write_json_atomic(workspace.path_for(root / "data/drafts/current_draft.json"), {
        "student_name": f"QA-DRAFT-{user}", "author_name": "QA",
        "counseling_date": now[:10], "transcript_text": f"QA-TRANSCRIPT-{user}",
    })
    audio = workspace.path_for(root / "data/uploads/synthetic.wav")
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"QA idle recording; never processed")
    job = create_audio_batch_job(
        audio_path=str(audio), source_name=audio.name, student_name=f"QA-AUDIO-{user}", author_name="QA",
        counseling_date=now[:10], processing_mode="qa", summary_style="qa", use_vad=False, use_prompt_hint=False,
    )
    batch = create_audio_batch_state([job], now)
    batch["status"] = "paused"
    save_audio_batch_state(workspace.path_for(root / "data/drafts/audio_batch_queue.json"), batch)
    csv_state = create_log_csv_batch_state(
        upload_signature=f"sha-{user}", source_csv_name=f"{user}.csv", source_csv_size=12, source_csv_path="",
        source_mtime_ns=0, selected_indices=list(range(count)), started_at=now, run_id=f"qa-{user}",
    )
    csv_state["status"] = "interrupted"
    save_log_csv_batch_state(workspace.path_for(root / "data/drafts/log_csv_to_docs_run.json"), csv_state)
    return auth


def test_app_shows_only_own_draft_and_progress_after_refresh(
    isolated_app: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = seed_user(isolated_app, "first", 1), seed_user(isolated_app, "second", 2)

    def render(header: str | None, view: AppTest | None = None) -> AppTest:
        headers = {ASSERTION_HEADER: [header]} if header else {}
        monkeypatch.setattr(st, "context", SimpleNamespace(
            headers=SimpleNamespace(get_all=lambda name: headers.get(name, [])),
        ))
        current = view if view is not None else AppTest.from_file(str(isolated_app / "app.py"), default_timeout=25)
        return current.run()

    for user, header, count in (
        ("first", first, 1), ("second", second, 2), ("first", assertion("first"), 1),
    ):
        view = render(header)
        assert not view.exception
        assert view.session_state["student_name"] == f"QA-DRAFT-{user}"
        assert view.session_state["transcript_text"] == f"QA-TRANSCRIPT-{user}"
        assert view.session_state["audio_batch_state"]["jobs"][0]["student_name"] == f"QA-AUDIO-{user}"
        warnings = [str(element.value) for element in view.warning]
        assert any(f"0/{count}" in value for value in warnings)
        assert not any(f"0/{3 - count}" in value for value in warnings)
    for denied in (render(None), render(second, render(first))):
        assert not denied.exception and denied.error and not denied.text_input
    assert not (isolated_app / "data/drafts/current_draft.json").exists()


def test_two_csv_workers_keep_checkpoints_separate_and_verify_lost_response(
    isolated_app: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = load_app(isolated_app, None)
    users = ("a", "b")
    workspaces = {user: verified_workspace(isolated_app, [assertion(user)], QA_SECRET) for user in users}
    entered = {user: threading.Event() for user in users}
    release = threading.Event()
    writes: dict[str, int] = {}
    errors: list[BaseException] = []

    class NoStreamlitState:
        def get(self, key: str, default: str = "") -> str:
            raise AssertionError("A background worker must not consult browser session state")

    monkeypatch.setattr(app.st, "session_state", NoStreamlitState())
    monkeypatch.setattr(app, "GOOGLE_DOCS_TIMEOUT_VERIFY_PAUSE_SECONDS", 0)

    def fake_write(url: str, name: str, *_: object, **kwargs: object) -> dict[str, bool | str]:
        assert url == "https://example.invalid/exec"
        assert kwargs["preserve_existing_sections"] is True
        writes[name] = writes.get(name, 0) + 1
        entered[name].set()
        assert release.wait(10), "QA transfer was not released"
        if name == "a":
            raise app.GoogleDocsPostTimeoutError("QA", "response lost after save")
        return {"ok": True, "message": "saved"}

    def fake_read(url: str, name: str, *_: object, **kwargs: object) -> dict[str, bool]:
        assert url == "https://example.invalid/exec"
        assert kwargs["preserve_existing_sections"] is True
        return {"ok": True, "verify_only": True, "found_existing": name in writes}

    monkeypatch.setattr(app, "append_summary_to_google_doc", fake_write)
    monkeypatch.setattr(app, "verify_summary_in_google_doc_after_timeout", fake_read)

    def start(user: str) -> threading.Thread:
        workspace: AdminWorkspace = workspaces[user]
        args = transfer_args(app)
        rows = [{"date": "2026-08-01", "name": user, "sid": user, "notes": "QA not a real record", "subject": "math"}]
        args["matched_rows"] = app.match_log_rows_to_docs(rows, [{"student_name": user, "doc_id": f"qa-{user}"}])
        args["process_selected_indices"] = args["run_scope_indices"] = [0]
        args["run_state"] = create_log_csv_batch_state(
            upload_signature=f"fixture-{user}", source_csv_name=f"qa-{user}.csv", source_csv_size=1,
            source_csv_path="", source_mtime_ns=0, selected_indices=[0], started_at=stamp(), run_id=f"run-{user}",
        )

        def work() -> None:
            with use_worker_workspace(workspace):
                app.execute_log_csv_transfer(**args)

        return app.start_csv_worker(workspace.path_for(app.LOG_CSV_WORKER_LOCK_PATH), work, errors.append)

    threads: list[threading.Thread] = []
    try:
        threads.append(start("a"))
        assert entered["a"].wait(5)
        threads.append(start("b"))
        assert entered["b"].wait(5), "The second user was blocked by the first user's lease"
        for user in users:
            path = workspaces[user].path_for(app.LOG_CSV_DOCS_STATE_PATH)
            checkpoint = app.load_log_csv_batch_state(path)
            assert checkpoint["run_id"] == f"run-{user}"
            assert checkpoint["completed_count"] == 0
    finally:
        release.set()
        for thread in threads:
            thread.join(10)
    assert all(not thread.is_alive() for thread in threads)
    assert not errors, errors
    assert writes == {"a": 1, "b": 1}
    for user in users:
        ws = workspaces[user]
        result = app.load_log_csv_batch_state(ws.path_for(app.LOG_CSV_DOCS_STATE_PATH))
        assert result["status"] == "completed"
        row, = result["results"]
        assert row["name"] == user
        assert app.classify_google_docs_save_result(row)["count_key"] == (
            "saved_confirmed_after_timeout" if user == "a" else "new_saved"
        )


@pytest.mark.parametrize("already_written", [False, True])
def test_resume_verifies_only_unfinished_row_before_reposting(
    isolated_app: Path, monkeypatch: pytest.MonkeyPatch, already_written: bool,
) -> None:
    app = load_app(isolated_app, None)
    workspace = verified_workspace(isolated_app, [assertion("resume")], QA_SECRET)
    args = transfer_args(app)
    first = {"source_index": 0, "name": "TEST0", "result_status": "성공", "doc_id": "test-doc-0"}
    state = app.record_log_csv_batch_result(args["run_state"], source_index=0, result=first, failed=False, updated_at=stamp())
    state = app.set_log_csv_batch_in_flight_requests(
        state, requests=[in_flight_request(app, args["matched_rows"][1], 1)], updated_at=stamp(),
    )
    state = app.interrupt_log_csv_batch_state(state, error="synthetic disconnect", updated_at=stamp())
    path = workspace.path_for(app.LOG_CSV_DOCS_STATE_PATH)
    save_log_csv_batch_state(path, state)
    restarted = app.load_log_csv_batch_state(path)
    args["run_state"] = app.resume_log_csv_batch_state(restarted, updated_at=stamp())
    args["results"] = restarted["results"]
    args["process_selected_indices"] = [index for index in restarted["selected_indices"] if index not in restarted["completed_indices"]]
    args["completed_before"] = restarted["completed_count"]
    events: list[tuple[str, str]] = []
    exists = already_written

    def read(url: str, name: str, *_: object, **__: object) -> dict[str, bool]:
        events.append(("verify", name))
        return {"ok": True, "verify_only": True, "found_existing": exists}

    def write(url: str, name: str, *_: object, **__: object) -> dict[str, bool | str]:
        nonlocal exists
        events.append(("write", name))
        exists = True
        return {"ok": True, "message": "saved"}

    monkeypatch.setattr(app, "append_summary_to_google_doc", write)
    monkeypatch.setattr(app, "verify_summary_in_google_doc_after_timeout", read)
    monkeypatch.setattr(app, "GOOGLE_DOCS_TIMEOUT_VERIFY_PAUSE_SECONDS", 0)
    with use_worker_workspace(workspace):
        app.execute_log_csv_transfer(**args)
    assert events == [("verify", "TEST1")] + ([] if already_written else [("write", "TEST1"), ("verify", "TEST1")])
    finished = app.load_log_csv_batch_state(path)
    assert finished["status"] == "completed" and finished["completed_indices"] == [0, 1]
    assert len(finished["results"]) == 2
