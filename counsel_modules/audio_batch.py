from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from counsel_modules.atomic_json import write_json_atomic


AUDIO_BATCH_MAX_FILES = 30

AudioUploadMode = Literal["empty", "single", "batch", "over_limit"]
AudioBatchJobStatus = Literal["pending", "processing", "completed", "needs_review", "failed"]
AudioBatchStatus = Literal["running", "paused", "completed"]


class AudioBatchJob(TypedDict):
    job_id: str
    audio_path: str
    source_name: str
    student_name: str
    author_name: str
    counseling_date: str
    processing_mode: str
    summary_style: str
    use_vad: bool
    use_prompt_hint: bool
    status: AudioBatchJobStatus
    detail: str
    transcript_path: str
    summary: str
    attempts: int
    updated_at: str
    admin_saved: NotRequired[bool]
    admin_sid: NotRequired[str]
    admin_saved_export: NotRequired[str]
    docs_saved: NotRequired[bool]
    dual_save_sid: NotRequired[str]
    dual_save_doc_id: NotRequired[str]
    dual_save_subject: NotRequired[str]
    recording_replay_key: NotRequired[str]


class AudioBatchState(TypedDict):
    version: int
    status: AudioBatchStatus
    total: int
    jobs: list[AudioBatchJob]
    updated_at: str
    acknowledged_at: NotRequired[str]


class AudioBatchCounts(TypedDict):
    pending: int
    processing: int
    completed: int
    needs_review: int
    failed: int


@dataclass(frozen=True, slots=True)
class AudioBatchLimitError(ValueError):
    selected_count: int

    def __str__(self) -> str:
        return f"녹음 파일은 한 번에 최대 {AUDIO_BATCH_MAX_FILES}개까지 처리할 수 있습니다. ({self.selected_count}개 선택됨)"


def classify_audio_upload_selection(selected_count: int) -> AudioUploadMode:
    if selected_count <= 0:
        return "empty"
    if selected_count == 1:
        return "single"
    if selected_count <= AUDIO_BATCH_MAX_FILES:
        return "batch"
    return "over_limit"


def create_audio_batch_job(
    *,
    audio_path: str,
    source_name: str,
    student_name: str,
    author_name: str,
    counseling_date: str,
    processing_mode: str,
    summary_style: str,
    use_vad: bool,
    use_prompt_hint: bool,
) -> AudioBatchJob:
    identity = "|".join(
        [
            audio_path.replace("\\", "/").lower(),
            student_name.strip(),
            counseling_date,
        ]
    )
    job_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return {
        "job_id": job_id,
        "audio_path": audio_path,
        "source_name": source_name,
        "student_name": student_name.strip(),
        "author_name": author_name.strip(),
        "counseling_date": counseling_date,
        "processing_mode": processing_mode,
        "summary_style": summary_style,
        "use_vad": bool(use_vad),
        "use_prompt_hint": bool(use_prompt_hint),
        "status": "pending",
        "detail": "",
        "transcript_path": "",
        "summary": "",
        "attempts": 0,
        "updated_at": "",
        "admin_saved": False,
        "admin_sid": "",
        "admin_saved_export": "",
        "recording_replay_key": "",
    }


def create_audio_batch_state(jobs: list[AudioBatchJob], updated_at: str) -> AudioBatchState:
    if not jobs or len(jobs) > AUDIO_BATCH_MAX_FILES:
        raise AudioBatchLimitError(len(jobs))
    return {
        "version": 1,
        "status": "running",
        "total": len(jobs),
        "jobs": [job.copy() for job in jobs],
        "updated_at": updated_at,
        "acknowledged_at": "",
    }


def audio_batch_counts(state: AudioBatchState) -> AudioBatchCounts:
    counts: AudioBatchCounts = {
        "pending": 0,
        "processing": 0,
        "completed": 0,
        "needs_review": 0,
        "failed": 0,
    }
    for job in state["jobs"]:
        counts[job["status"]] += 1
    return counts


def next_pending_audio_batch_job(state: AudioBatchState) -> tuple[int, AudioBatchJob] | None:
    for index, job in enumerate(state["jobs"]):
        if job["status"] == "pending":
            return index, job.copy()
    return None


def update_audio_batch_job(
    state: AudioBatchState,
    job_id: str,
    *,
    status: AudioBatchJobStatus,
    detail: str,
    transcript_path: str,
    summary: str,
    updated_at: str,
    increment_attempts: bool = False,
) -> AudioBatchState:
    jobs: list[AudioBatchJob] = []
    found = False
    for current in state["jobs"]:
        job = current.copy()
        if job["job_id"] == job_id:
            found = True
            job["status"] = status
            job["detail"] = detail
            job["transcript_path"] = transcript_path
            if summary != job["summary"]:
                if job.get("admin_saved") and not job.get("admin_saved_export"):
                    job["admin_saved_export"] = job["summary"]
                job["admin_saved"] = False
                job["docs_saved"] = False
            job["summary"] = summary
            job["updated_at"] = updated_at
            if increment_attempts:
                job["attempts"] += 1
        jobs.append(job)
    if not found:
        raise KeyError(job_id)

    has_unfinished = any(job["status"] in {"pending", "processing"} for job in jobs)
    batch_status: AudioBatchStatus = state["status"] if has_unfinished else "completed"
    return {
        "version": state["version"],
        "status": batch_status,
        "total": len(jobs),
        "jobs": jobs,
        "updated_at": updated_at,
        "acknowledged_at": state.get("acknowledged_at", ""),
    }


def set_audio_batch_status(
    state: AudioBatchState,
    status: AudioBatchStatus,
    updated_at: str,
) -> AudioBatchState:
    return {
        "version": state["version"],
        "status": status,
        "total": state["total"],
        "jobs": [job.copy() for job in state["jobs"]],
        "updated_at": updated_at,
        "acknowledged_at": state.get("acknowledged_at", ""),
    }


def recover_interrupted_audio_batch_state(
    state: AudioBatchState,
    updated_at: str,
) -> AudioBatchState:
    jobs: list[AudioBatchJob] = []
    for current in state["jobs"]:
        job = current.copy()
        if job["status"] == "processing":
            job["status"] = "pending"
            job["detail"] = "이전 실행이 중단되어 이어서 처리 대기 중"
            job["updated_at"] = updated_at
        jobs.append(job)

    has_unfinished = any(job["status"] == "pending" for job in jobs)
    recovered_status: AudioBatchStatus = "paused" if has_unfinished else "completed"
    return {
        "version": state["version"],
        "status": recovered_status,
        "total": len(jobs),
        "jobs": jobs,
        "updated_at": updated_at,
        "acknowledged_at": state.get("acknowledged_at", ""),
    }


def recover_stalled_audio_batch_state(
    state: AudioBatchState,
    updated_at: str,
    *,
    runtime_scheduled: bool,
) -> AudioBatchState:
    if state["status"] != "running" or runtime_scheduled:
        return state
    return recover_interrupted_audio_batch_state(state, updated_at)


def reconcile_audio_batch_paths(
    state: AudioBatchState,
    updated_at: str,
) -> AudioBatchState:
    jobs: list[AudioBatchJob] = []
    changed = False
    for current in state["jobs"]:
        job = current.copy()
        if job["status"] in {"pending", "processing"}:
            audio_exists = bool(job["audio_path"] and Path(job["audio_path"]).is_file())
            transcript_exists = bool(
                job["transcript_path"] and Path(job["transcript_path"]).is_file()
            )
            if not audio_exists and not transcript_exists:
                job["status"] = "needs_review"
                job["detail"] = "원본 녹음과 저장 원문을 찾지 못해 직접 확인 필요"
                job["updated_at"] = updated_at
                changed = True
        jobs.append(job)
    if not changed:
        return state

    has_unfinished = any(job["status"] in {"pending", "processing"} for job in jobs)
    has_problem = any(job["status"] in {"needs_review", "failed"} for job in jobs)
    if has_unfinished:
        status: AudioBatchStatus = state["status"]
    elif has_problem:
        status = "paused"
    else:
        status = "completed"
    return {
        "version": state["version"],
        "status": status,
        "total": len(jobs),
        "jobs": jobs,
        "updated_at": updated_at,
        "acknowledged_at": state.get("acknowledged_at", ""),
    }


def resume_audio_batch_state(state: AudioBatchState, updated_at: str) -> AudioBatchState:
    if not any(job["status"] == "pending" for job in state["jobs"]):
        return set_audio_batch_status(state, "completed", updated_at)
    return set_audio_batch_status(state, "running", updated_at)


def prepare_audio_batch_retry(state: AudioBatchState, updated_at: str, *, require_docs: bool = False) -> AudioBatchState:
    jobs: list[AudioBatchJob] = []
    for current in state["jobs"]:
        job = current.copy()
        incomplete = not job.get("admin_saved") or (require_docs and not job.get("docs_saved"))
        if job["status"] in {"failed", "needs_review"} or (job["status"] == "completed" and incomplete):
            job["status"] = "pending"
            job["detail"] = "문제 파일 다시 처리 대기 중"
            job["updated_at"] = updated_at
        jobs.append(job)

    has_pending = any(job["status"] == "pending" for job in jobs)
    batch_status: AudioBatchStatus = "running" if has_pending else "completed"
    return {
        "version": state["version"],
        "status": batch_status,
        "total": len(jobs),
        "jobs": jobs,
        "updated_at": updated_at,
        "acknowledged_at": state.get("acknowledged_at", ""),
    }


def load_audio_batch_state(path: Path) -> AudioBatchState | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_jobs = payload["jobs"]
        if not isinstance(raw_jobs, list) or not raw_jobs:
            return None
        jobs: list[AudioBatchJob] = []
        valid_statuses = {"pending", "processing", "completed", "needs_review", "failed"}
        for raw_job in raw_jobs:
            if not isinstance(raw_job, dict) or raw_job.get("status") not in valid_statuses:
                return None
            jobs.append(
                {
                    "job_id": str(raw_job["job_id"]),
                    "audio_path": str(raw_job["audio_path"]),
                    "source_name": str(raw_job["source_name"]),
                    "student_name": str(raw_job["student_name"]),
                    "author_name": str(raw_job["author_name"]),
                    "counseling_date": str(raw_job["counseling_date"]),
                    "processing_mode": str(raw_job["processing_mode"]),
                    "summary_style": str(raw_job["summary_style"]),
                    "use_vad": bool(raw_job.get("use_vad", False)),
                    "use_prompt_hint": bool(raw_job.get("use_prompt_hint", False)),
                    "status": raw_job["status"],
                    "detail": str(raw_job.get("detail", "")),
                    "transcript_path": str(raw_job.get("transcript_path", "")),
                    "summary": str(raw_job.get("summary", "")),
                    "attempts": int(raw_job.get("attempts", 0)),
                    "updated_at": str(raw_job.get("updated_at", "")),
                    "admin_saved": raw_job.get("admin_saved") is True,
                    "admin_sid": str(raw_job.get("admin_sid", "")),
                    "admin_saved_export": str(raw_job.get("admin_saved_export", "")),
                    "docs_saved": raw_job.get("docs_saved") is True,
                    "dual_save_sid": str(raw_job.get("dual_save_sid", "")),
                    "dual_save_doc_id": str(raw_job.get("dual_save_doc_id", "")),
                    "dual_save_subject": str(raw_job.get("dual_save_subject", "")),
                    "recording_replay_key": str(raw_job.get("recording_replay_key", "")),
                }
            )
        raw_status = str(payload.get("status", "paused"))
        status: AudioBatchStatus = raw_status if raw_status in {"running", "paused", "completed"} else "paused"
        return {
            "version": int(payload.get("version", 1)),
            "status": status,
            "total": len(jobs),
            "jobs": jobs,
            "updated_at": str(payload.get("updated_at", "")),
            "acknowledged_at": str(payload.get("acknowledged_at", "")),
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_audio_batch_state(path: Path, state: AudioBatchState) -> None:
    write_json_atomic(path, state)


def delete_audio_batch_state(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True
