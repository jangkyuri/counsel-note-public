# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["streamlit>=1.63,<2", "requests", "send2trash", "pyperclip", "imageio-ffmpeg"]
# ///
# Run from the preview checkout: python -B -m scripts.browser_workspace_qa
from __future__ import annotations

import json
import os
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from typing import Final, NotRequired, TypedDict, Unpack
from unittest.mock import patch
from zoneinfo import ZoneInfo

import requests

from counsel_modules.audio_batch import create_audio_batch_job, create_audio_batch_state, save_audio_batch_state
from scripts.run_browser_workspace_preview import run_preview

SOURCE: Final = Path(__file__).resolve().parents[1]
FAKE_ADMIN: Final = "https://connect-log-qa.invalid"
A: Final = "QA-BROWSER-A"
B: Final = "QA-BROWSER-B"
SUMMARY: Final = ("*\uC0C1\uB2F4 \uCC38\uACE0\uC0AC\uD56D*\n"
                  "- \uAD50\uC7AC 42\uCABD \uC624\uB2F5 3\uBB38\uC81C\uB97C \uB2E4\uC2DC \uD480\uC5B4\uC624\uAE30")


class PatchBody(TypedDict):
    sid: str
    base: dict[str, str]
    entry: dict[str, str]


class RequestOptions(TypedDict):
    params: NotRequired[dict[str, str]]
    json: NotRequired[PatchBody]
    timeout: NotRequired[int]
    headers: NotRequired[dict[str, str]]


def seed_workspace(draft_path: Path, label: str) -> None:
    """Seed a checkpoint with one completed item and, for A, one interrupted item."""
    workspace = draft_path.parents[2]
    audio = workspace / "data" / "uploads" / "synthetic.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"QA placeholder; no transcription should be invoked")
    now = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    jobs = [
        create_audio_batch_job(
            audio_path=str(audio), source_name=audio.name, student_name=f"{label}-{number}",
            author_name=label, counseling_date=now[:10], processing_mode="", summary_style="",
            use_vad=False, use_prompt_hint=False,
        )
        for number in range(1, 3 if label == A else 2)
    ]
    jobs[0].update(status="completed", admin_saved=True, admin_sid=f"{label}-1", summary=SUMMARY, attempts=1)
    if label == A:
        jobs[1].update(status="processing", summary=SUMMARY, attempts=1)
    state = create_audio_batch_state(jobs, now)
    if label == B:
        state["status"] = "completed"
    save_audio_batch_state(draft_path.with_name("audio_batch_queue.json"), state)
    print(f"Seeded {label}", flush=True)


def seed_when_drafts_appear(root: Path, stop: Event) -> None:
    seen: set[Path] = set()
    while not stop.wait(0.25):
        for draft in (root / "data" / "admin_workspaces").glob("*/data/drafts/current_draft.json"):
            if draft in seen:
                continue
            label = json.loads(draft.read_text(encoding="utf-8")).get("author_name")
            if label in (A, B):
                seed_workspace(draft, label)
                seen.add(draft)


def run_qa(root: Path) -> None:
    """Run only an isolated copy and deny unrecognized HTTP destinations."""
    for name in ("app.py", "requirements.txt", "APP_VERSION.txt", "BUILD_INFO.json"):
        shutil.copyfile(SOURCE / name, root / name)
    for name in ("counsel_modules", "hdhcounsel", "prompts"):
        shutil.copytree(SOURCE / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    students = root / "data" / "students"
    students.mkdir(parents=True)
    for filename, content in {
        "google_docs_map.csv": "student_name,doc_id,doc_url\n",
        "google_apps_script_webapp_url.txt": "",
        "admin_api_base_url.txt": FAKE_ADMIN,
        "admin_sid_map.csv": f"student_name,admin_sid\n{A}-2,{A}-2\n",
        "recording_auto_cleanup_enabled.txt": "false",
    }.items():
        (students / filename).write_text(content, encoding="utf-8")
    profile = root / "profile"
    profile.mkdir()
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = str(profile)
    for key in (
        "OPENAI_API_KEY", "CONNECT_LOG_DOCS_QA_URL", "CONNECT_LOG_DOCS_QA_ID",
        "CONNECT_LOG_DOCS_QA_BEARER_TOKEN",
    ):
        os.environ.pop(key, None)
    os.environ["CONNECT_LOG_AI_BACKEND"] = "openai"
    os.environ["CONNECT_LOG_BROWSER_WORKSPACE_SECRET"] = secrets.token_hex(32)
    notes = "QA-KEEP"
    writes = 0

    def local_response(_session: requests.Session, method: str, url: str, **options: Unpack[RequestOptions]) -> requests.Response:
        nonlocal notes, writes
        if (method.upper(), url) == ("GET", f"{FAKE_ADMIN}/api/students"):
            payload = []
        elif (method.upper(), url) == ("GET", f"{FAKE_ADMIN}/api/logs/entry"):
            assert options["params"]["sid"] == f"{A}-2"
            payload = {"ok": True, "entry": {"notes": notes}}
        elif (method.upper(), url) == ("POST", f"{FAKE_ADMIN}/api/logs/patch"):
            body = options["json"]
            assert body["sid"] == f"{A}-2" and body["base"]["notes"] == notes
            notes = body["entry"]["notes"]
            writes += 1
            assert writes == 1 and "QA-KEEP" in notes
            print("QA saved one missing item; original notes preserved", flush=True)
            payload = {"ok": True}
        else:
            raise AssertionError("External or unrecognized HTTP request denied in browser QA")
        response = requests.Response()
        response.status_code = 200
        response.encoding = "utf-8"
        response._content = json.dumps(payload).encode("utf-8")
        return response

    stop = Event()
    seed = Thread(target=seed_when_drafts_appear, args=(root, stop), daemon=True)
    seed.start()
    try:
        with patch.object(requests.Session, "request", local_response):
            run_preview(root / "app.py")
    finally:
        stop.set()
        seed.join()


if __name__ == "__main__":
    with TemporaryDirectory(prefix="cl8511-") as folder:
        run_qa(Path(folder))
