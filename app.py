from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from textwrap import dedent
from zoneinfo import ZoneInfo

import pyperclip
import streamlit as st
import streamlit.components.v1 as components
from streamlit.delta_generator import DeltaGenerator
from streamlit.errors import StreamlitInvalidLayoutContextError


_STARTUP_REQUIRED_FILES = [
    "counsel_modules/__init__.py",
    "counsel_modules/audio_batch.py",
    "counsel_modules/audio_batch_review.py",
    "counsel_modules/codex_runner.py",
    "counsel_modules/codex_runtime.py",
    "counsel_modules/log_csv_batch_state.py",
    "counsel_modules/atomic_json.py",
    "counsel_modules/log_csv_worker.py",
    "counsel_modules/log_csv_processing_history.py",
    "counsel_modules/log_csv_saved_students.py",
    "counsel_modules/log_csv_recheck.py",
    "counsel_modules/log_csv_utils.py",
    "counsel_modules/runtime_utils.py",
    "counsel_modules/text_utils.py",
    "counsel_modules/google_docs_utils.py",
    "counsel_modules/admin_utils.py",
    "prompts/summary_ko.txt",
    "data/students/google_docs_map.csv",
    "data/students/google_apps_script_webapp_url.txt",
]


def _check_startup_required_files() -> None:
    app_root = Path(__file__).resolve().parent
    missing = [relative_path for relative_path in _STARTUP_REQUIRED_FILES if not (app_root / relative_path).exists()]
    if not missing:
        return

    st.error("\uc571 \uc2e4\ud589\uc5d0 \ud544\uc694\ud55c \ud30c\uc77c\uc774 \ub204\ub77d\ub418\uc5c8\uc2b5\ub2c8\ub2e4.")
    st.warning("\ucd5c\uc2e0 \ubc30\ud3ec zip\uc744 \ub2e4\uc2dc \ubc1b\uc544 \uc555\ucd95\uc744 \ud480\uc5b4\uc8fc\uc138\uc694.")
    st.markdown("**\ub204\ub77d\ub41c \ud30c\uc77c**")
    for relative_path in missing:
        expected_path = app_root / relative_path
        st.write(f"- `{relative_path}`")
        st.caption(f"  \uae30\ub300 \uacbd\ub85c: {expected_path}")
    st.stop()


_check_startup_required_files()


from counsel_modules.audio_batch_review import AudioBatchReviewActions, render_audio_batch_summary_review
from counsel_modules.log_csv_saved_students import confirmed_students_from_history_export
from counsel_modules.log_csv_recheck import (
    LogCsvRecheckActions, LogCsvStorageCheck, LogCsvStorageResponse,
    classify_log_csv_storage_check, render_completed_log_csv_recheck,
)
from counsel_modules.log_csv_utils import (
    LOG_EXPORT_CONTENT_COLUMNS,
    build_log_csv_dedupe_key,
    classify_google_docs_save_result,
    log_csv_confirmed_student_keys,
    log_row_has_docs_content,
    match_log_rows_to_docs,
    merge_homework_duplicate_log_rows,
    normalize_log_notes_text,
    short_preview_text,
    validate_log_export_columns,
)
from counsel_modules.log_csv_batch_state import (
    LogCsvBatchState,
    acknowledge_log_csv_batch_state,
    complete_log_csv_batch_state,
    create_log_csv_batch_state,
    interrupt_log_csv_batch_state,
    load_log_csv_batch_state,
    log_csv_batch_in_flight_requests,
    log_csv_batch_state_is_recently_running,
    log_csv_batch_state_matches,
    record_log_csv_batch_result,
    remaining_log_csv_batch_indices,
    resume_log_csv_batch_state,
    save_log_csv_batch_state,
    set_log_csv_batch_in_flight_requests,
    set_log_csv_batch_phase,
    should_release_log_csv_session_lock,
)
from counsel_modules.log_csv_worker import (
    CsvRunBusyError,
    CsvRunLease,
    csv_worker_is_running,
    start_csv_worker,
)
from counsel_modules.log_csv_processing_history import (
    LogCsvHistoryOutcome,
    LogCsvHistoryStart,
    LogCsvHistoryUpdate,
    LogCsvProcessingHistory,
    LogCsvProcessingHistoryError,
    count_new_log_csv_rows,
    csv_bytes_sha256,
    empty_log_csv_processing_history,
    finalize_log_csv_history_entry,
    find_log_csv_history_entry,
    fingerprint_log_csv_rows,
    load_log_csv_processing_history,
    log_csv_history_can_seed_from_completed_batch,
    log_csv_history_is_completed,
    log_csv_history_processing_is_finished,
    log_csv_history_source_indices,
    prepare_log_csv_history_entry,
    recent_log_csv_history_day_summaries,
    record_log_csv_history_outcome,
    save_log_csv_processing_history,
    unresolved_log_csv_history_indices,
    upsert_log_csv_history_entry,
)
from counsel_modules.admin_utils import (
    admin_notes_preview_key as _admin_notes_preview_key,
    admin_student_detail_from_row as _admin_student_detail_from_row,
    admin_student_name_from_row as _admin_student_name_from_row,
    admin_student_sid_from_row as _admin_student_sid_from_row,
    auto_select_admin_sid_candidate as _auto_select_admin_sid_candidate,
    extract_admin_students_from_payload as _extract_admin_students_from_payload,
    format_admin_sid_candidate_label as _format_admin_sid_candidate_label,
    public_admin_sid_candidate_detail as _public_admin_sid_candidate_detail,
)
from counsel_modules.audio_batch import (
    AUDIO_BATCH_MAX_FILES,
    AudioBatchJob,
    AudioBatchJobStatus,
    AudioBatchState,
    audio_batch_counts,
    classify_audio_upload_selection,
    create_audio_batch_job,
    create_audio_batch_state,
    delete_audio_batch_state,
    load_audio_batch_state,
    next_pending_audio_batch_job,
    prepare_audio_batch_retry,
    recover_interrupted_audio_batch_state,
    reconcile_audio_batch_paths,
    recover_stalled_audio_batch_state,
    resume_audio_batch_state,
    save_audio_batch_state,
    set_audio_batch_status,
    update_audio_batch_job,
)
from counsel_modules.codex_runner import (
    CodexExecutable,
    CodexExecutionError,
    CodexLoginStatus,
    build_codex_invocations as _build_codex_invocations,
    check_codex_login_status as _check_codex_login_status,
    classify_codex_failure_category as _classify_codex_failure_category,
    codex_env as _codex_env,
    find_codex_executable as _find_codex_executable,
    fixed_windows_codex_path as _fixed_windows_codex_path,
    is_windowsapps_path as _is_windowsapps_path,
    launch_codex_login_window as _launch_codex_login_window,
    run_codex_invocations,
)
from counsel_modules.google_docs_utils import (
    classify_google_docs_timeout_verification,
    clean_google_apps_script_url as _clean_google_apps_script_url,
    extract_doc_id_from_url as _extract_doc_id_from_url,
    google_docs_append_key as _google_docs_append_key,
    has_hidden_url_prefix as _has_hidden_url_prefix,
    is_google_docs_lock_timeout_message as _is_google_docs_lock_timeout_message,
)
from counsel_modules.local_consumed_admin_notes import (
    CONSUMED_ADMIN_NOTES_PATH,
    diagnose_consumed_admin_notes,
    extract_consumable_admin_section_notes_from_docs_text,
    filter_consumed_admin_sections_from_docs_text,
    filter_consumed_admin_notes,
    load_consumed_admin_notes,
    mark_admin_notes_consumed,
    reset_consumed_admin_notes,
)
from counsel_modules.runtime_utils import (
    canonical_app_dir,
    format_app_last_modified_badge as _format_app_last_modified_badge,
    format_build_info_badge as _format_build_info_badge,
    format_build_info_last_updated as _format_build_info_last_updated,
    is_expected_runtime_base_dir as _is_expected_runtime_base_dir,
    normalize_runtime_path as _normalize_runtime_path,
    recording_auto_cleanup_enabled as _recording_auto_cleanup_enabled,
    read_app_version as _read_app_version,
    read_build_info as _read_build_info,
    runtime_environment_diagnostics as _runtime_environment_diagnostics,
)
from counsel_modules.text_utils import (
    build_admin_today_focus_block,
    detect_suspicious_transcript,
    extract_student_name_from_audio_filename as _extract_student_name_from_audio_filename,
    extract_marked_summary_output,
    extract_today_focus,
    extract_learning_flow_section,
    ensure_required_today_focus_section as _ensure_required_today_focus_section,
    format_summary_section_spacing as _format_summary_section_spacing,
    format_elapsed as _format_elapsed,
    format_timestamp as _format_timestamp,
    format_transcript_file as _format_transcript_file,
    merge_next_action_sections_into_notes as _merge_next_action_sections_into_notes,
    merge_today_focus_block,
    move_today_check_section_to_top as _move_today_check_section_to_top,
    google_docs_primary_book_name,
    normalize_legacy_check_sections as _normalize_legacy_check_sections,
    normalize_summary_identity as _normalize_summary_identity,
    parse_draft_counseling_date as _parse_draft_counseling_date,
    remove_generic_today_focus_items as _remove_generic_today_focus_items,
    remove_empty_placeholder_lines as _remove_empty_placeholder_lines,
    remove_meta_uncertainty_notes as _remove_meta_uncertainty_notes,
    remove_misc_record_section as _remove_misc_record_section,
    remove_parent_message_sections as _remove_parent_message_sections,
    remove_runtime_noise_from_summary,
    remove_status_detail_labels as _remove_status_detail_labels,
    prepare_summary_for_google_docs_cell,
    remove_today_focus_for_google_docs,
    safe_date_for_filename as _safe_date_for_filename,
    sanitize_filename_part as _sanitize_filename_part,
    summary_has_meaningful_body_content as _summary_has_meaningful_body_content,
    summary_notes_items,
    summary_has_required_notes as _summary_has_required_notes,
    summary_has_required_today_focus as _summary_has_required_today_focus,
    runtime_noise_line_count,
    split_transcript_sentences,
)

class WorkflowStepError(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


class InsufficientTranscriptError(WorkflowStepError):
    pass


class GoogleDocsPostTimeoutError(WorkflowStepError):
    pass


class GoogleDocsPostIndeterminateError(WorkflowStepError):
    pass


class GoogleDocsLockTimeoutError(WorkflowStepError):
    pass


@dataclass(frozen=True, slots=True)
class LocalLogCsvUpload:
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size(self) -> int:
        return self.path.stat().st_size

    def getvalue(self) -> bytes:
        return self.path.read_bytes()


@dataclass(frozen=True, slots=True)
class AdminNotesSyncRequest:
    student_name: str
    counseling_date: date
    admin_sid: str
    admin_base_url: str
    export_text: str
    export_source_label: str
    today_focus_text: str
    preview_key: str


@dataclass(frozen=True, slots=True)
class AdminNotesSyncResult:
    updated: bool
    notes: str
    backup_path: Path | None
    response: dict[str, object] | None


BASE_DIR = Path(__file__).resolve().parent
TRANSCRIPT_DIR = BASE_DIR / "data" / "transcripts"
DRAFT_DIR = BASE_DIR / "data" / "drafts"
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
TMP_DIR = BASE_DIR / "data" / "tmp"
FFMPEG_BIN_DIR = BASE_DIR / "tools" / "ffmpeg" / "bin"
WHISPER_AUDIO_TMP_DIR = TMP_DIR / "audio_for_whisper"
EXTRACT_ERROR_LOG_DIR = BASE_DIR / "_debug_audio" / "extract_errors"
ADMIN_API_DEBUG_DIR = BASE_DIR / "_admin_api_debug"
GLOSSARY_DIR = BASE_DIR / "data" / "glossary"
GLOSSARY_PATH = GLOSSARY_DIR / "academy_terms.csv"
STUDENTS_DIR = BASE_DIR / "data" / "students"
GOOGLE_DOCS_MAP_PATH = STUDENTS_DIR / "google_docs_map.csv"
GOOGLE_APPS_SCRIPT_URL_PATH = STUDENTS_DIR / "google_apps_script_webapp_url.txt"
ADMIN_SID_MAP_PATH = STUDENTS_DIR / "admin_sid_map.csv"
ADMIN_API_BASE_URL_PATH = STUDENTS_DIR / "admin_api_base_url.txt"
LOCAL_ADMIN_SID_OVERRIDES_PATH = BASE_DIR / "data" / "local_admin_sid_overrides.json"
ADMIN_NOTES_BACKUP_DIR = BASE_DIR / "data" / "admin_notes_backups"
GOOGLE_DOCS_APPEND_LOG_DIR = BASE_DIR / "data" / "google_docs_append_logs"
LOG_CSV_DOCS_STATE_PATH = DRAFT_DIR / "log_csv_to_docs_run.json"
LOG_CSV_WORKER_LOCK_PATH = DRAFT_DIR / "log_csv_worker.lock"
LOG_CSV_PROCESSING_HISTORY_PATH = BASE_DIR / "data" / "local_log_csv_processing_history.json"
GOOGLE_DOCS_MAP_BACKUP_ROOT = BASE_DIR.parent / "counsel-note_BACKUPS"
CURRENT_DRAFT_PATH = DRAFT_DIR / "current_draft.json"
AUDIO_BATCH_STATE_PATH = DRAFT_DIR / "audio_batch_queue.json"
PROMPT_PATH = BASE_DIR / "prompts" / "summary_ko.txt"
APP_VERSION_PATH = BASE_DIR / "APP_VERSION.txt"
BUILD_INFO_PATH = BASE_DIR / "BUILD_INFO.json"
EXPECTED_RUNTIME_BASE_DIR = canonical_app_dir()
DEFAULT_ADMIN_API_BASE_URL = "http://192.168.0.5:8003"
ALLOWED_EXTENSIONS = {"mp3", "m4a", "wav"}
GOOGLE_DOCS_POST_TIMEOUT_SECONDS = 60
GOOGLE_DOCS_VERIFY_TIMEOUT_SECONDS = 30
GOOGLE_DOCS_TIMEOUT_VERIFY_PAUSE_SECONDS = 7
LOG_CSV_TO_DOCS_BATCH_SIZE_OPTIONS = (3, 5, 10)
LOG_CSV_TO_DOCS_PACING_VERSION = 2
LOG_CSV_TO_DOCS_DEFAULT_BATCH_SIZE = 10
LOG_CSV_TO_DOCS_MAX_WORKERS = 5
LOG_CSV_TO_DOCS_BATCH_PAUSE_SECONDS = 3
LOG_CSV_TO_DOCS_ROW_PAUSE_SECONDS = 0.5
LOG_CSV_TO_DOCS_LOCK_RETRY_PAUSE_SECONDS = 12
LOG_CSV_AUTO_IMPORT_POLL_INTERVAL = "2s"
LOG_CSV_AUTO_IMPORT_STABLE_NS = 1_000_000_000
LOG_CSV_DOWNLOAD_NAME_PATTERN = re.compile(
    r"^수업기록_(?:선택일|완료미정리)_\d{4}-\d{2}-\d{2}(?: \(\d+\))?\.csv$"
)
GOOGLE_DOCS_TIMEOUT_REVIEW_REASON = (
    "응답 시간 초과: 실제 Google Docs에는 이미 추가됐을 수 있으므로 "
    "문서 확인 후 선택적으로 재시도하세요."
)
GOOGLE_DOCS_LOCK_TIMEOUT_REVIEW_REASON = (
    "Apps Script 잠금 시간초과로 저장되지 않았을 가능성이 높습니다. "
    "잠시 후 해당 항목만 재시도하세요."
)
RECORDING_CLEANUP_EXTENSIONS = {"m4a", "mp3", "wav", "webm", "aac", "flac"}
APP_MAINTENANCE_CLEANUP_POLICIES = [
    {
        "name": "임시 파일",
        "root": TMP_DIR,
        "days": 2,
        "extensions": None,
        "recursive": True,
    },
    {
        "name": "원문 추출 오류 로그",
        "root": EXTRACT_ERROR_LOG_DIR,
        "days": 30,
        "extensions": {"txt", "log"},
        "recursive": True,
    },
    {
        "name": "admin API 디버그 로그",
        "root": ADMIN_API_DEBUG_DIR,
        "days": 30,
        "extensions": {"txt", "log", "json"},
        "recursive": True,
    },
    {
        "name": "Google Docs 저장 결과 로그",
        "root": GOOGLE_DOCS_APPEND_LOG_DIR,
        "days": 7,
        "extensions": {"csv"},
        "recursive": False,
    },
    {
        "name": "admin 특이사항 백업",
        "root": ADMIN_NOTES_BACKUP_DIR,
        "days": 180,
        "extensions": {"txt"},
        "recursive": False,
    },
]
PROCESSING_MODES = {
    "HDH 로컬 화자 전사": {"model_size": "large-v3", "beam_size": 5, "compute_type": "hdh-local"},
    "GPU 터보 모드": {"model_size": "large-v3-turbo", "beam_size": 1, "compute_type": "openvino-int8"},
    "빠른 모드": {"model_size": "base", "beam_size": 1, "compute_type": "int8"},
    "균형 모드": {"model_size": "small", "beam_size": 3, "compute_type": "int8"},
    "정확 모드": {"model_size": "small", "beam_size": 5, "compute_type": "int8"},
    "최고 정확 모드": {"model_size": "medium", "beam_size": 5, "compute_type": "int8"},
}
WHISPER_INITIAL_PROMPT = "한국어 학원 상담 녹음입니다."
DEFAULT_GLOSSARY_CSV = """category,term,aliases,note
교재,쎈,센;쌘;ssen,수학 문제집
교재,RPM,알피엠;아르피엠,수학 문제집
교재,Word Master,워드마스터;월드마스터,영단어 교재
커리큘럼,중등 문법 1단계,문법 일단계;중등문법1,내부 커리큘럼
시험,단어 테스트,단테;단어시험,어휘 확인
"""
DEFAULT_REQUIREMENTS = """streamlit
faster-whisper
pyperclip
send2trash
requests
"""
GLOSSARY_PROMPT_LIMIT = 200


def prepend_path(path: Path) -> None:
    if not path.exists():
        return
    path_text = str(path)
    current_entries = os.environ.get("PATH", "").split(os.pathsep)
    if any(entry.lower() == path_text.lower() for entry in current_entries):
        return
    os.environ["PATH"] = path_text + os.pathsep + os.environ.get("PATH", "")


def read_app_version() -> str:
    return _read_app_version(APP_VERSION_PATH)


def read_build_info() -> dict[str, object]:
    info = dict(_read_build_info(BUILD_INFO_PATH))
    if not any(str(value or "").strip() for value in info.values()):
        return {}
    return info


def format_build_info_last_updated(build_info: dict[str, object] | None = None) -> str:
    source = build_info if build_info is not None else read_build_info()
    info = _read_build_info(BUILD_INFO_PATH)
    for key in info:
        info[key] = str((source or {}).get(key, "") or "")
    return _format_build_info_last_updated(info)


def format_build_info_badge(build_info: dict[str, object] | None = None) -> str:
    source = build_info if build_info is not None else read_build_info()
    info = _read_build_info(BUILD_INFO_PATH)
    for key in info:
        info[key] = str((source or {}).get(key, "") or "")
    return _format_build_info_badge(info)


def format_app_last_modified_badge(base_dir: Path | None = None) -> str:
    return _format_app_last_modified_badge(base_dir or BASE_DIR)


def normalize_runtime_path_for_compare(path: Path) -> str:
    return _normalize_runtime_path(path)


def is_expected_runtime_base_dir(current_path: Path | None = None) -> bool:
    current = current_path or BASE_DIR
    return _is_expected_runtime_base_dir(current)


def runtime_environment_diagnostics() -> dict[str, object]:
    return dict(
        _runtime_environment_diagnostics(
            base_dir=BASE_DIR,
            app_py_path=Path(__file__).resolve(),
            app_version_path=APP_VERSION_PATH,
            build_info_path=BUILD_INFO_PATH,
        )
    )


prepend_path(FFMPEG_BIN_DIR)


def ensure_default_glossary_file() -> None:
    if GLOSSARY_PATH.exists():
        return
    GLOSSARY_DIR.mkdir(parents=True, exist_ok=True)
    GLOSSARY_PATH.write_text(DEFAULT_GLOSSARY_CSV, encoding="utf-8")


def ensure_admin_settings_files() -> None:
    STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
    ADMIN_NOTES_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not ADMIN_SID_MAP_PATH.exists():
        ADMIN_SID_MAP_PATH.write_text("student_name,admin_sid\n", encoding="utf-8")
    if not ADMIN_API_BASE_URL_PATH.exists():
        ADMIN_API_BASE_URL_PATH.write_text(DEFAULT_ADMIN_API_BASE_URL, encoding="utf-8")


def ensure_project_structure() -> list[str]:
    warnings: list[str] = []
    for folder in [
        BASE_DIR / "data",
        TRANSCRIPT_DIR,
        DRAFT_DIR,
        UPLOAD_DIR,
        TMP_DIR,
        GLOSSARY_DIR,
        STUDENTS_DIR,
        ADMIN_NOTES_BACKUP_DIR,
        GOOGLE_DOCS_APPEND_LOG_DIR,
        BASE_DIR / "prompts",
        BASE_DIR / "scripts",
    ]:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.append(f"{folder} 폴더를 만들 수 없습니다: {exc}")

    requirements_path = BASE_DIR / "requirements.txt"
    if not requirements_path.exists():
        try:
            requirements_path.write_text(DEFAULT_REQUIREMENTS, encoding="utf-8")
        except OSError as exc:
            warnings.append(f"requirements.txt 파일을 만들 수 없습니다: {exc}")

    return warnings


def check_required_project_files() -> list[dict[str, str]]:
    checks = [
        ("app.py", BASE_DIR / "app.py", "오류"),
        ("requirements.txt", BASE_DIR / "requirements.txt", "주의"),
        ("prompts/summary_ko.txt", PROMPT_PATH, "오류"),
        ("data/students/google_docs_map.csv", GOOGLE_DOCS_MAP_PATH, "주의"),
        ("data/students/google_apps_script_webapp_url.txt", GOOGLE_APPS_SCRIPT_URL_PATH, "주의"),
        ("data/students/admin_sid_map.csv", ADMIN_SID_MAP_PATH, "주의"),
        ("data/students/admin_api_base_url.txt", ADMIN_API_BASE_URL_PATH, "주의"),
    ]
    issues: list[dict[str, str]] = []
    for label, path, severity in checks:
        if not path.exists():
            issues.append({"파일": label, "상태": severity, "경로": str(path)})
    return issues


def render_startup_warnings(structure_warnings: list[str], file_issues: list[dict[str, str]]) -> None:
    if not structure_warnings and not file_issues:
        return

    blocking = [issue for issue in file_issues if issue["상태"] == "오류"]
    if blocking:
        st.error("앱 실행에 필요한 파일이 없습니다. 아래 항목을 확인하세요.")
    else:
        st.warning("일부 설정 파일이 없습니다. 기본 기능은 사용할 수 있지만 관련 기능이 제한될 수 있습니다.")

    with st.expander("누락 파일 / 폴더 확인", expanded=bool(blocking)):
        for warning in structure_warnings:
            st.write(f"- 폴더 생성 실패: `{warning}`")
        for issue in file_issues:
            st.write(f"- {issue['상태']}: `{issue['파일']}`")
            st.caption(issue["경로"])


def apply_ui_refresh_styles() -> None:
    st.html(
        """
        <style>
          [data-testid="stElementContainer"] { transition: none !important; }
          [data-testid="stElementContainer"][data-stale="true"],
          [data-testid="stExpander"] > details,
          [data-testid="stExpander"] > details > summary {
            opacity: 1 !important;
            transition: none !important;
          }
        </style>
        """
    )


def apply_korean_text_input_hints() -> None:
    components.html(
        """
        <script>
        (() => {
          const labels = new Set(["학생명", "작성자"]);
          const apply = () => {
            const doc = window.parent.document;
            doc.documentElement.setAttribute("lang", "ko");
            doc.querySelectorAll("input").forEach((input) => {
              const label = input.getAttribute("aria-label") || "";
              const placeholder = input.getAttribute("placeholder") || "";
              if (labels.has(label) || placeholder.includes("학생명")) {
                input.setAttribute("lang", "ko");
                input.setAttribute("inputmode", "text");
                input.setAttribute("autocomplete", "off");
                input.setAttribute("autocapitalize", "off");
                input.setAttribute("spellcheck", "false");
              }
            });
          };
          apply();
          new MutationObserver(apply).observe(window.parent.document.body, { childList: true, subtree: true });
        })();
        </script>
        """,
        height=0,
    )


def academy_glossary_mtime() -> float:
    try:
        return GLOSSARY_PATH.stat().st_mtime
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False)
def load_academy_glossary_cached(file_mtime: float) -> list[dict[str, str | list[str]]]:
    del file_mtime
    if not GLOSSARY_PATH.exists():
        return []
    terms: list[dict[str, str | list[str]]] = []
    try:
        with GLOSSARY_PATH.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                term = (row.get("term") or "").strip()
                if not term:
                    continue
                aliases = [
                    alias.strip()
                    for alias in (row.get("aliases") or "").split(";")
                    if alias.strip()
                ]
                terms.append(
                    {
                        "category": (row.get("category") or "").strip(),
                        "term": term,
                        "aliases": aliases,
                        "note": (row.get("note") or "").strip(),
                    }
                )
    except Exception:
        return []
    return terms


def load_academy_glossary() -> list[dict[str, str | list[str]]]:
    return load_academy_glossary_cached(academy_glossary_mtime())


def google_docs_map_mtime() -> float:
    try:
        return GOOGLE_DOCS_MAP_PATH.stat().st_mtime
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False)
def load_google_docs_map_cached(file_mtime: float) -> dict[str, dict[str, str]]:
    del file_mtime
    if not GOOGLE_DOCS_MAP_PATH.exists():
        return {}
    mapping: dict[str, dict[str, str]] = {}
    try:
        with GOOGLE_DOCS_MAP_PATH.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                student_name = (row.get("student_name") or "").strip()
                if not student_name:
                    continue
                mapping[student_name] = {
                    "doc_url": (row.get("doc_url") or "").strip(),
                    "doc_id": (row.get("doc_id") or "").strip(),
                }
    except Exception:
        return {}
    return mapping


def load_google_docs_map() -> dict[str, dict[str, str]]:
    return load_google_docs_map_cached(google_docs_map_mtime())


@st.cache_data(show_spinner=False)
def load_google_docs_map_rows_cached(file_mtime: float) -> list[dict[str, str]]:
    del file_mtime
    if not GOOGLE_DOCS_MAP_PATH.exists():
        return []
    rows: list[dict[str, str]] = []
    try:
        with GOOGLE_DOCS_MAP_PATH.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                student_name = (row.get("student_name") or "").strip()
                doc_url = (row.get("doc_url") or "").strip()
                doc_id = (row.get("doc_id") or "").strip()
                if not student_name:
                    continue
                rows.append(
                    {
                        "student_name": student_name,
                        "doc_url": doc_url,
                        "doc_id": doc_id,
                    }
                )
    except Exception:
        return []
    return rows


def load_google_docs_map_rows() -> list[dict[str, str]]:
    return load_google_docs_map_rows_cached(google_docs_map_mtime())


def extract_google_docs_doc_id(value: str | None) -> str:
    return _extract_doc_id_from_url(value)


def normalize_google_docs_doc_url(value: str, doc_id: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    return f"https://docs.google.com/document/d/{doc_id}/edit"


def backup_google_docs_map_file(timestamp: str) -> Path:
    backup_dir = GOOGLE_DOCS_MAP_BACKUP_ROOT / f"google_docs_map_BACKUP_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / "google_docs_map.csv"
    if GOOGLE_DOCS_MAP_PATH.exists():
        shutil.copy2(GOOGLE_DOCS_MAP_PATH, backup_path)
    else:
        backup_path.write_text("student_name,doc_url,doc_id\n", encoding="utf-8-sig")
    return backup_path


def append_google_docs_mapping(student_name: str, doc_url_or_id: str) -> dict[str, str]:
    clean_name = re.sub(r"\s+", " ", str(student_name or "").strip())
    raw_url = str(doc_url_or_id or "").strip()
    doc_id = extract_google_docs_doc_id(raw_url)
    if not clean_name:
        raise WorkflowStepError("Google Docs 매핑 추가", "학생명을 입력하세요.")
    if not raw_url:
        raise WorkflowStepError("Google Docs 매핑 추가", "Google Docs URL을 입력하세요.")
    if not doc_id:
        raise WorkflowStepError("Google Docs 매핑 추가", "Google Docs URL에서 doc_id를 찾지 못했습니다.")

    required_columns = ["student_name", "doc_url", "doc_id"]
    if GOOGLE_DOCS_MAP_PATH.exists() and GOOGLE_DOCS_MAP_PATH.stat().st_size > 0:
        with GOOGLE_DOCS_MAP_PATH.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            missing_columns = [
                column for column in required_columns if column not in (reader.fieldnames or [])
            ]
        if missing_columns:
            raise WorkflowStepError(
                "Google Docs 매핑 추가",
                "google_docs_map.csv 컬럼을 확인하세요: "
                + ", ".join(missing_columns),
            )

    existing_rows = load_google_docs_map_rows()
    duplicate_names = [
        row for row in existing_rows if (row.get("student_name") or "").strip() == clean_name
    ]
    duplicate_doc_ids = [
        row for row in existing_rows if (row.get("doc_id") or "").strip() == doc_id
    ]
    if duplicate_names:
        existing = duplicate_names[0]
        raise WorkflowStepError(
            "Google Docs 매핑 추가",
            "이미 같은 학생명 매핑이 있습니다: "
            f"{clean_name} · doc_id: {existing.get('doc_id') or '-'} · "
            f"doc_url: {existing.get('doc_url') or '-'}",
        )
    if duplicate_doc_ids:
        owner = (duplicate_doc_ids[0].get("student_name") or "-").strip()
        raise WorkflowStepError(
            "Google Docs 매핑 추가",
            f"이미 같은 doc_id가 등록되어 있습니다: {owner} · doc_id: {doc_id}",
        )

    timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    backup_path = backup_google_docs_map_file(timestamp)
    GOOGLE_DOCS_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = GOOGLE_DOCS_MAP_PATH.exists() and GOOGLE_DOCS_MAP_PATH.stat().st_size > 0
    with GOOGLE_DOCS_MAP_PATH.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["student_name", "doc_url", "doc_id"])
        if not file_exists:
            writer.writeheader()
        writer.writerow(
            {
                "student_name": clean_name,
                "doc_url": normalize_google_docs_doc_url(raw_url, doc_id),
                "doc_id": doc_id,
            }
        )
    load_google_docs_map_cached.clear()
    load_google_docs_map_rows_cached.clear()
    return {"student_name": clean_name, "doc_id": doc_id, "backup_path": str(backup_path)}


def google_docs_mapping_registration_rows(
    rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    display_rows: list[dict[str, str]] = []
    for row in rows:
        student_name = (row.get("student_name") or "").strip()
        if not student_name:
            continue
        doc_url = (row.get("doc_url") or "").strip()
        doc_id = (row.get("doc_id") or "").strip()
        if not doc_url and doc_id:
            doc_url = normalize_google_docs_doc_url("", doc_id)
        display_rows.append({"학생명": student_name, "Docs": doc_url})
    return display_rows


def render_google_docs_mapping_add_ui(ui_locked: bool) -> None:
    add_success = st.session_state.pop("google_docs_map_add_success", "")
    if add_success:
        st.success(add_success)

    with st.expander("신규생 Google Docs 등록", expanded=bool(add_success)):
        new_docs_student_name = st.text_input(
            "학생명",
            key="new_google_docs_mapping_student_name",
            disabled=ui_locked,
        )
        new_docs_url = st.text_input(
            "Google Docs URL",
            key="new_google_docs_mapping_url",
            disabled=ui_locked,
        )
        if st.button("등록", key="new_google_docs_mapping_add_button", disabled=ui_locked):
            try:
                add_result = append_google_docs_mapping(new_docs_student_name, new_docs_url)
            except WorkflowStepError as exc:
                st.warning(str(exc))
            except OSError as exc:
                st.error(f"Google Docs 매핑 저장에 실패했습니다: {exc}")
            else:
                st.success(
                    f"신규생 Docs 매핑을 등록했습니다: {add_result['student_name']} "
                    f"(doc_id: {add_result['doc_id']})"
                )

        mapping_rows = google_docs_mapping_registration_rows(load_google_docs_map_rows())
        st.divider()
        st.caption(f"등록 목록 · google_docs_map.csv · {len(mapping_rows):,}건")
        if mapping_rows:
            st.dataframe(
                mapping_rows,
                use_container_width=True,
                hide_index=True,
                height=360,
                column_config={
                    "Docs": st.column_config.LinkColumn("Docs", display_text="열기"),
                },
            )
        else:
            st.caption("등록된 학생 Docs가 없습니다.")


def analyze_student_mapping_health() -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    docs_rows = load_google_docs_map_rows()
    docs_by_name: dict[str, list[dict[str, str]]] = {}
    for row in docs_rows:
        student_name = (row.get("student_name") or "").strip()
        if not student_name:
            continue
        docs_by_name.setdefault(student_name, []).append(row)
        doc_url = (row.get("doc_url") or "").strip()
        doc_id = (row.get("doc_id") or "").strip()
        if not doc_url:
            issues.append(
                {
                    "구분": "Google Docs",
                    "학생명": student_name,
                    "상태": "주의",
                    "내용": "doc_url 비어 있음",
                }
            )
        elif not doc_url.startswith("https://docs.google.com/"):
            issues.append(
                {
                    "구분": "Google Docs",
                    "학생명": student_name,
                    "상태": "주의",
                    "내용": "doc_url 형식 확인 필요",
                }
            )
        if not doc_id:
            issues.append(
                {
                    "구분": "Google Docs",
                    "학생명": student_name,
                    "상태": "오류",
                    "내용": "doc_id 비어 있음",
                }
            )

    for student_name, rows in docs_by_name.items():
        if len(rows) > 1:
            doc_ids = sorted({(row.get("doc_id") or "-").strip() for row in rows})
            issues.append(
                {
                    "구분": "Google Docs",
                    "학생명": student_name,
                    "상태": "주의",
                    "내용": f"동일 학생명 매핑 {len(rows)}개 · doc_id: {', '.join(doc_ids[:4])}",
                }
            )

    admin_rows = load_admin_sid_map()
    admin_by_name: dict[str, list[dict[str, str]]] = {}
    for row in admin_rows:
        student_name = (row.get("student_name") or "").strip()
        admin_sid = (row.get("admin_sid") or "").strip()
        if not student_name:
            continue
        admin_by_name.setdefault(student_name, []).append(row)
        if not admin_sid:
            issues.append(
                {
                    "구분": "admin SID",
                    "학생명": student_name,
                    "상태": "오류",
                    "내용": "admin_sid 비어 있음",
                }
            )

    for student_name, rows in admin_by_name.items():
        unique_sids = sorted({(row.get("admin_sid") or "").strip() for row in rows if (row.get("admin_sid") or "").strip()})
        if len(rows) > 1:
            issues.append(
                {
                    "구분": "admin SID",
                    "학생명": student_name,
                    "상태": "주의",
                    "내용": f"동일 학생명 매핑 {len(rows)}개 · SID: {', '.join(unique_sids[:4]) or '-'}",
                }
            )

    docs_names = set(docs_by_name)
    admin_names = set(admin_by_name)
    for student_name in sorted(admin_names - docs_names):
        issues.append(
            {
                "구분": "매핑 교차점검",
                "학생명": student_name,
                "상태": "주의",
                "내용": "admin SID는 있으나 Google Docs 매핑 없음",
            }
        )
    for student_name in sorted(docs_names - admin_names):
        issues.append(
            {
                "구분": "매핑 교차점검",
                "학생명": student_name,
                "상태": "주의",
                "내용": "Google Docs는 있으나 admin SID 연결 정보 없음",
            }
        )
    return issues


def find_google_docs_mapping(
    student_name: str | None,
    mapping: dict[str, dict[str, str]] | None = None,
) -> dict[str, str] | None:
    clean_name = (student_name or "").strip()
    if not clean_name:
        return None
    docs_map = mapping if mapping is not None else load_google_docs_map()
    item = docs_map.get(clean_name)
    if not item:
        return None
    if not item.get("doc_url") and not item.get("doc_id"):
        return None
    return item


def load_google_apps_script_webapp_url() -> str:
    try:
        return clean_google_apps_script_url(
            GOOGLE_APPS_SCRIPT_URL_PATH.read_text(encoding="utf-8-sig")
        )
    except OSError:
        return ""


def clean_google_apps_script_url(value: str | None) -> str:
    return _clean_google_apps_script_url(value)


def has_hidden_url_prefix(value: str | None) -> bool:
    return _has_hidden_url_prefix(value)


def validate_google_apps_script_url(url: str) -> None:
    clean_url = clean_google_apps_script_url(url)
    if not clean_url:
        raise WorkflowStepError("Google Docs \uc124\uc815", "Google Apps Script Web App URL\uc744 \uc785\ub825\ud558\uc138\uc694.")
    if not clean_url.startswith("https://script.google.com/macros/s/"):
        raise WorkflowStepError(
            "Google Docs \uc124\uc815",
            "Apps Script Web App URL\uc774 \uc62c\ubc14\ub974\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4. https://script.google.com/macros/s/.../exec \ud615\uc2dd\uc774\uc5b4\uc57c \ud569\ub2c8\ub2e4.",
        )


def google_apps_script_url_notice(url: str) -> str:
    clean_url = clean_google_apps_script_url(url)
    if clean_url and not clean_url.endswith("/exec"):
        return "URL\uc774 /exec\ub85c \ub05d\ub098\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4. Apps Script \uc6f9\uc571 \ubc30\ud3ec URL\uc778\uc9c0 \ud655\uc778\ud558\uc138\uc694."
    return ""


def save_google_apps_script_webapp_url(url: str) -> dict[str, str]:
    if not STUDENTS_DIR.exists():
        raise WorkflowStepError("Google Docs \uc124\uc815", f"\ud559\uc0dd \uc124\uc815 \ud3f4\ub354\uac00 \uc5c6\uc2b5\ub2c8\ub2e4: {STUDENTS_DIR}")

    clean_url = clean_google_apps_script_url(url)
    validate_google_apps_script_url(clean_url)

    backup_path = ""
    backup_error = ""
    if GOOGLE_APPS_SCRIPT_URL_PATH.exists():
        timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d-%H%M%S")
        backup_file = GOOGLE_APPS_SCRIPT_URL_PATH.with_name(
            f"{GOOGLE_APPS_SCRIPT_URL_PATH.name}.bak-{timestamp}"
        )
        try:
            shutil.copy2(GOOGLE_APPS_SCRIPT_URL_PATH, backup_file)
            backup_path = str(backup_file)
        except OSError as exc:
            backup_error = str(exc)

    GOOGLE_APPS_SCRIPT_URL_PATH.write_text(clean_url + "\n", encoding="utf-8")
    return {"backup_path": backup_path, "backup_error": backup_error}


def render_google_apps_script_url_settings_ui(ui_locked: bool) -> None:
    with st.expander("Google Apps Script \uc6f9\uc571 URL \uc124\uc815", expanded=False):
        saved_web_app_url = load_google_apps_script_webapp_url()
        raw_saved_web_app_url = ""
        try:
            raw_saved_web_app_url = GOOGLE_APPS_SCRIPT_URL_PATH.read_text(encoding="utf-8-sig")
        except OSError:
            pass

        if has_hidden_url_prefix(raw_saved_web_app_url):
            st.warning("Web App URL \uc55e\uc5d0 \uc228\uc740 \ubb38\uc790(BOM)\uac00 \uac10\uc9c0\ub418\uc5b4 \uc800\uc7a5 \uc2dc \uc81c\uac70\ub429\ub2c8\ub2e4.")

        web_app_url_input = st.text_input(
            "Google Apps Script Web App URL",
            value=saved_web_app_url,
            placeholder="https://script.google.com/macros/s/.../exec",
            key="google_apps_script_webapp_url_input",
            disabled=ui_locked,
        )
        notice = google_apps_script_url_notice(web_app_url_input)
        if notice:
            st.warning(notice)

        if st.button("Web App URL \uc800\uc7a5", disabled=ui_locked, key="google_apps_script_webapp_url_save"):
            try:
                save_result = save_google_apps_script_webapp_url(web_app_url_input)
                st.success("Google Apps Script Web App URL\uc744 \uc800\uc7a5\ud588\uc2b5\ub2c8\ub2e4.")
                if save_result.get("backup_error"):
                    st.warning(f"\uae30\uc874 URL \ubc31\uc5c5\uc740 \uc2e4\ud328\ud588\uc9c0\ub9cc \uc0c8 URL\uc740 \uc800\uc7a5\ud588\uc2b5\ub2c8\ub2e4: {save_result['backup_error']}")
            except WorkflowStepError as exc:
                st.warning(str(exc))
            except OSError as exc:
                st.error(f"Web App URL \uc800\uc7a5\uc5d0 \uc2e4\ud328\ud588\uc2b5\ub2c8\ub2e4: {exc}")


def google_docs_append_key(student_name: str, counseling_date_value: date, summary: str) -> str:
    return _google_docs_append_key(student_name, counseling_date_value, summary)


def is_google_docs_lock_timeout_message(message: object) -> bool:
    return _is_google_docs_lock_timeout_message(message)


def append_summary_to_google_doc(
    web_app_url: str,
    student_name: str,
    counseling_date_value: date,
    summary: str,
    doc_id: str,
    dedupe_key: str | None = None,
    subject: str | None = None,
    book: str | None = None,
    post_request=None,
) -> dict[str, object]:
    cleaned_web_app_url = clean_google_apps_script_url(web_app_url)
    validate_google_apps_script_url(cleaned_web_app_url)
    if not cleaned_web_app_url:
        raise WorkflowStepError("Google Docs 추가", "Google Apps Script Web App URL이 없습니다.")
    if not doc_id.strip():
        raise WorkflowStepError("Google Docs 추가", "현재 학생의 Google Docs 문서가 등록되어 있지 않습니다.")
    summary_for_google_docs = prepare_summary_for_google_docs_cell(
        summary,
        subject=subject,
        student_name=student_name,
        curriculum=subject,
        book=book,
        counseling_date=counseling_date_value,
    )
    if not summary_for_google_docs.strip():
        raise WorkflowStepError("Google Docs 추가", "Google Docs에 추가할 정리본이 비어 있습니다.")

    try:
        import requests
    except Exception as exc:
        raise WorkflowStepError(
            "Google Docs 추가",
            "requests 패키지를 불러올 수 없습니다. pip install -r requirements.txt를 실행하세요.",
        ) from exc

    payload = {
        "student_name": student_name.strip(),
        "counseling_date": counseling_date_value.isoformat(),
        "summary": summary_for_google_docs,
        "doc_id": doc_id.strip(),
        "dedupe_key": (
            dedupe_key or google_docs_append_key(student_name, counseling_date_value, summary_for_google_docs)
        ).strip(),
    }
    try:
        request_callable = post_request or requests.post
        response = request_callable(
            cleaned_web_app_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=GOOGLE_DOCS_POST_TIMEOUT_SECONDS,
        )
    except (requests.exceptions.ReadTimeout, requests.exceptions.Timeout) as exc:
        raise GoogleDocsPostTimeoutError(
            "Google Docs 추가",
            f"{GOOGLE_DOCS_TIMEOUT_REVIEW_REASON} ({exc})",
        ) from exc
    except Exception as exc:
        if is_google_docs_lock_timeout_message(exc):
            raise GoogleDocsLockTimeoutError(
                "Google Docs 추가",
                f"{GOOGLE_DOCS_LOCK_TIMEOUT_REVIEW_REASON} ({exc})",
            ) from exc
        raise WorkflowStepError("Google Docs 추가", str(exc)) from exc

    response_text = response.text or ""
    response_preview = response_text[:500]
    try:
        data = response.json()
    except Exception as exc:
        if is_google_docs_lock_timeout_message(response_preview):
            raise GoogleDocsLockTimeoutError(
                "Google Docs 추가",
                f"{GOOGLE_DOCS_LOCK_TIMEOUT_REVIEW_REASON} · HTTP {response.status_code} · 응답: {response_preview}",
            ) from exc
        raise GoogleDocsPostIndeterminateError(
            "Google Docs 추가",
            f"응답 JSON 파싱 실패 · HTTP {response.status_code} · 응답: {response_preview}",
        ) from exc

    if not isinstance(data, dict):
        raise GoogleDocsPostIndeterminateError(
            "Google Docs 추가",
            f"예상하지 못한 응답 · HTTP {response.status_code} · 응답: {response_preview}",
        )

    response_error_text = data.get("error") or data.get("result_message") or data.get("message") or ""
    if is_google_docs_lock_timeout_message(response_error_text):
        raise GoogleDocsLockTimeoutError(
            "Google Docs 추가",
            f"{GOOGLE_DOCS_LOCK_TIMEOUT_REVIEW_REASON} · HTTP {response.status_code} · {response_error_text}",
        )

    if data.get("ok") is True:
        return data

    error = data.get("error") or data.get("message") or response_preview or "오류 내용 없음"
    if is_google_docs_lock_timeout_message(error):
        raise GoogleDocsLockTimeoutError(
            "Google Docs 추가",
            f"{GOOGLE_DOCS_LOCK_TIMEOUT_REVIEW_REASON} · HTTP {response.status_code} · {error}",
        )
    raise WorkflowStepError(
        "Google Docs 추가",
        f"Apps Script 오류 · HTTP {response.status_code} · {error}",
    )



def verify_summary_in_google_doc_after_timeout(
    web_app_url: str,
    student_name: str,
    counseling_date_value: date,
    summary: str,
    doc_id: str,
    dedupe_key: str | None = None,
    subject: str | None = None,
    book: str | None = None,
    post_request=None,
) -> dict[str, object]:
    cleaned_web_app_url = clean_google_apps_script_url(web_app_url)
    try:
        validate_google_apps_script_url(cleaned_web_app_url)
    except WorkflowStepError as exc:
        return {
            "ok": False,
            "verify_only": True,
            "error_type": "invalid_web_app_url",
            "message": str(exc),
        }
    if not cleaned_web_app_url or not doc_id.strip():
        return {
            "ok": False,
            "verify_only": True,
            "error_type": "missing_target",
            "message": "Google Apps Script URL 또는 doc_id가 없어 verify-only 확인을 건너뜁니다.",
        }

    summary_for_google_docs = prepare_summary_for_google_docs_cell(
        summary,
        subject=subject,
        student_name=student_name,
        curriculum=subject,
        book=book,
        counseling_date=counseling_date_value,
    )
    if not summary_for_google_docs.strip():
        return {
            "ok": False,
            "verify_only": True,
            "error_type": "empty_summary",
            "message": "Google Docs 전송용 본문이 비어 verify-only 확인을 건너뜁니다.",
        }

    try:
        import requests
    except Exception as exc:
        return {
            "ok": False,
            "verify_only": True,
            "error_type": "requests_unavailable",
            "message": str(exc),
        }

    payload = {
        "action": "verify_existing",
        "verify_only": True,
        "student_name": student_name.strip(),
        "counseling_date": counseling_date_value.isoformat(),
        "summary": summary_for_google_docs,
        "doc_id": doc_id.strip(),
        "dedupe_key": (
            dedupe_key or google_docs_append_key(student_name, counseling_date_value, summary_for_google_docs)
        ).strip(),
    }
    try:
        request_callable = post_request or requests.post
        response = request_callable(
            cleaned_web_app_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=GOOGLE_DOCS_VERIFY_TIMEOUT_SECONDS,
        )
    except (requests.exceptions.ReadTimeout, requests.exceptions.Timeout) as exc:
        return {
            "ok": False,
            "verify_only": True,
            "error_type": "verify_timeout",
            "message": str(exc),
        }
    except Exception as exc:
        return {
            "ok": False,
            "verify_only": True,
            "error_type": "verify_request_error",
            "message": str(exc),
        }

    response_text = response.text or ""
    response_preview = response_text[:500]
    try:
        data = response.json()
    except Exception:
        return {
            "ok": False,
            "verify_only": True,
            "error_type": "verify_json_error",
            "message": f"HTTP {response.status_code}: {response_preview}",
        }
    if not isinstance(data, dict):
        return {
            "ok": False,
            "verify_only": True,
            "error_type": "verify_unexpected_response",
            "message": f"HTTP {response.status_code}: {response_preview}",
        }
    data.setdefault("verify_only", True)
    return data


def read_log_export_csv(uploaded_file) -> tuple[list[dict[str, str]], list[str], str]:
    raw_bytes = uploaded_file.getvalue()
    last_error: Exception | None = None
    decoded_text = ""
    used_encoding = ""
    for encoding in ("utf-8-sig", "cp949"):
        try:
            decoded_text = raw_bytes.decode(encoding)
            used_encoding = encoding
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    if not used_encoding:
        raise WorkflowStepError(
            "로그 CSV 읽기",
            f"CSV 인코딩을 읽지 못했습니다. UTF-8 또는 cp949 파일인지 확인하세요: {last_error}",
        )

    reader = csv.DictReader(io.StringIO(decoded_text, newline=""))
    columns = [(column or "").strip().lstrip("\ufeff") for column in (reader.fieldnames or [])]
    reader.fieldnames = columns

    rows: list[dict[str, str]] = []
    for row in reader:
        clean_row = {
            (key or "").strip().lstrip("\ufeff"): (value or "").strip()
            for key, value in row.items()
            if key is not None
        }
        if any(clean_row.values()):
            rows.append(clean_row)

    return rows, columns, used_encoding


def default_log_csv_downloads_dir() -> Path:
    user_profile = os.environ.get("USERPROFILE", "").strip()
    return (Path(user_profile) if user_profile else Path.home()) / "Downloads"


def log_csv_auto_export_token(path: Path, mtime_ns: int) -> str:
    normalized_path = os.path.normcase(os.path.abspath(os.fspath(path)))
    token_source = f"{normalized_path}|{int(mtime_ns)}"
    return hashlib.sha256(token_source.encode("utf-8")).hexdigest()[:24]


def log_csv_batch_now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def load_current_log_csv_processing_history() -> LogCsvProcessingHistory | None:
    try:
        history = load_log_csv_processing_history(LOG_CSV_PROCESSING_HISTORY_PATH)
    except LogCsvProcessingHistoryError as exc:
        st.session_state["log_csv_processing_history_error"] = str(exc)
        return None
    st.session_state["log_csv_processing_history_error"] = ""
    return history


def persist_current_log_csv_processing_history(history: LogCsvProcessingHistory) -> bool:
    try:
        with CsvRunLease(LOG_CSV_WORKER_LOCK_PATH):
            merged = load_log_csv_processing_history(LOG_CSV_PROCESSING_HISTORY_PATH)
            for entry in history["entries"]:
                previous = find_log_csv_history_entry(merged, entry["csv_hash"])
                if previous is not None and (
                    previous["updated_at"] > entry["updated_at"]
                    or (
                        previous["updated_at"] == entry["updated_at"]
                        and previous["resolved_count"] > entry["resolved_count"]
                    )
                ):
                    continue
                merged = upsert_log_csv_history_entry(merged, entry)
            save_log_csv_processing_history(LOG_CSV_PROCESSING_HISTORY_PATH, merged)
    except (OSError, TypeError, ValueError, CsvRunBusyError, LogCsvProcessingHistoryError) as exc:
        st.session_state["log_csv_processing_history_error"] = str(exc)
        return False
    st.session_state["log_csv_processing_history_error"] = ""
    return True


def log_csv_legacy_batch_source_matches(
    state: LogCsvBatchState | None,
    uploaded_log_csv: object,
) -> bool:
    if state is None or not isinstance(uploaded_log_csv, LocalLogCsvUpload):
        return False
    source_path_text = str(state.get("source_csv_path") or "").strip()
    source_mtime_ns = int(state.get("source_mtime_ns") or 0)
    if not source_path_text or source_mtime_ns <= 0:
        return False
    try:
        source_path = Path(source_path_text)
        source_stat = source_path.stat()
        return bool(
            source_path.resolve() == uploaded_log_csv.path.resolve()
            and source_stat.st_mtime_ns == source_mtime_ns
            and int(state.get("source_csv_size") or 0) == uploaded_log_csv.size
        )
    except OSError:
        return False


def log_csv_history_source_indices_for_matches(
    matched_rows: list[dict[str, object]],
    matched_indices: list[int],
) -> list[int]:
    source_indices: list[int] = []
    for matched_index in matched_indices:
        if matched_index < 0 or matched_index >= len(matched_rows):
            continue
        for source_index in log_csv_history_source_indices(matched_rows[matched_index]):
            if source_index not in source_indices:
                source_indices.append(source_index)
    return source_indices


def log_csv_history_review_source_indices(
    matched_rows: list[dict[str, object]],
    selectable_indices: list[int],
) -> list[int]:
    selectable_index_set = set(selectable_indices)
    review_indices: list[int] = []
    for matched_index, item in enumerate(matched_rows):
        if matched_index in selectable_index_set:
            continue
        if str(item.get("status") or "").strip() == "기록 내용 없음":
            continue
        if not str(item.get("docs_preview_text") or "").strip():
            continue
        for source_index in log_csv_history_source_indices(item):
            if source_index not in review_indices:
                review_indices.append(source_index)
    return review_indices


def log_csv_history_outcome_from_result(result: dict[str, object]) -> LogCsvHistoryOutcome:
    count_key = str(classify_google_docs_save_result(result).get("count_key") or "")
    if count_key in {"new_saved", "updated_existing", "saved_confirmed_after_timeout"}:
        return "success"
    if count_key == "already_saved":
        return "duplicate"
    if count_key == "failed":
        return "failure"
    return "review"


def format_log_csv_history_time(timestamp: str) -> tuple[str, str]:
    try:
        completed_at = datetime.fromisoformat(timestamp).astimezone(ZoneInfo("Asia/Seoul"))
    except (TypeError, ValueError):
        return "날짜 미확인", "시각 미확인"
    return completed_at.strftime("%m-%d"), completed_at.strftime("%H:%M")


def create_audio_log_overview() -> tuple[DeltaGenerator, DeltaGenerator, DeltaGenerator]:
    st.subheader("녹음 파일")
    st.html(
        """
        <style>
          .st-key-audio-log-overview { container-type:inline-size; }
          .st-key-audio-log-overview [data-testid="stHorizontalBlock"] {
            display:grid;
            grid-template-columns:minmax(0,300px) minmax(0,1fr);
            gap:16px;
            align-items:start;
          }
          .st-key-audio-log-overview [data-testid="stColumn"] {
            width:100%;
            min-width:0;
          }
          .st-key-audio-log-overview [data-testid="stColumn"]:first-child {
            max-width:300px;
          }
          .st-key-audio-upload-input [data-testid="stFileUploaderDropzoneInstructions"] {
            display:none;
          }
          @container (max-width:640px) {
            .st-key-audio-log-overview [data-testid="stHorizontalBlock"] {
              grid-template-columns:minmax(0,1fr);
            }
          }
        </style>
        """
    )
    with st.container(key="audio-log-overview"):
        st.caption(f"상담 녹음 파일 업로드 · 최대 {AUDIO_BATCH_MAX_FILES}개")
        upload_col, history_col = st.columns([1, 1.35], gap="small")
        with upload_col:
            upload_placeholder = st.container(key="audio-upload-input")
            action_placeholder = st.container(key="audio-generate-action")
        with history_col:
            history_placeholder = st.container()
    return upload_placeholder, action_placeholder, history_placeholder


@st.cache_data(show_spinner=False, max_entries=256)
def load_log_csv_saved_student_keys(
    path_text: str, mtime_ns: int, size: int,
) -> set[tuple[str, str]]:
    with Path(path_text).open(encoding="utf-8-sig", newline="") as file:
        return log_csv_confirmed_student_keys(list(csv.DictReader(file)))


@st.cache_data(show_spinner=False, max_entries=256)
def load_log_csv_history_export(path_text: str, mtime_ns: int, size: int) -> tuple[str, list[dict[str, str]]]:
    raw = Path(path_text).read_bytes()
    rows, _columns, _encoding = read_log_export_csv(io.BytesIO(raw))
    return csv_bytes_sha256(raw), rows


def current_log_csv_saved_student_keys() -> set[tuple[str, str]] | None:
    saved_students: set[tuple[str, str]] = set()
    try:
        for path in GOOGLE_DOCS_APPEND_LOG_DIR.glob("log_csv_to_docs_result_*.csv"):
            stat = path.stat()
            saved_students.update(
                load_log_csv_saved_student_keys(str(path), stat.st_mtime_ns, stat.st_size)
            )
    except (OSError, UnicodeError, csv.Error):
        return None
    state = load_current_log_csv_batch_state()
    saved_students.update(log_csv_confirmed_student_keys((state or {}).get("results") or []))
    history = load_current_log_csv_processing_history()
    unverified_days: set[str] = set()
    if history:
        entries = {entry["csv_hash"]: entry for entry in history["entries"]}
        docs_rows = load_google_docs_map_rows()
        for path in default_log_csv_downloads_dir().glob("수업기록_*.csv"):
            if not LOG_CSV_DOWNLOAD_NAME_PATTERN.fullmatch(path.name):
                continue
            try:
                stat = path.stat()
                csv_hash, rows = load_log_csv_history_export(str(path), stat.st_mtime_ns, stat.st_size)
            except (OSError, UnicodeError, csv.Error, WorkflowStepError):
                continue
            entry = entries.get(csv_hash)
            if entry:
                recovered = confirmed_students_from_history_export(entry, rows, docs_rows)
                if recovered is None:
                    unverified_days.add(entry["source_date"])
                else:
                    saved_students.update(recovered)
    st.session_state["log_csv_student_count_unverified_days"] = unverified_days
    return saved_students


def render_log_csv_processing_history_summary(
    history: LogCsvProcessingHistory,
    placeholder: DeltaGenerator | None = None,
) -> DeltaGenerator:
    day_rows = []
    saved_students = current_log_csv_saved_student_keys()
    reference_date = datetime.now(ZoneInfo("Asia/Seoul")).date()
    for day_summary in recent_log_csv_history_day_summaries(
        history,
        reference_date=reference_date,
    ):
        source_date = date.fromisoformat(day_summary["source_date"])
        date_text = {0: "오늘", 1: "어제"}.get(
            (reference_date - source_date).days, source_date.strftime("%m-%d")
        )
        status_label = "완료" if day_summary["status"] == "completed" else "미완료"
        _timestamp_date, time_text = format_log_csv_history_time(day_summary["updated_at"])
        if not day_summary["has_history"]:
            time_text = "미처리"
        if saved_students is None:
            count_text = "인원 확인 필요"
        else:
            completed_students = sum(day == day_summary["source_date"] for day, _key in saved_students)
            has_confirmed_rows = any(
                entry["source_date"] == day_summary["source_date"]
                and (entry["success_count"] or entry["duplicate_count"])
                for entry in history["entries"]
            )
            uncertain_count = day_summary["source_date"] in st.session_state.get("log_csv_student_count_unverified_days", set())
            if uncertain_count or (not completed_students and has_confirmed_rows):
                count_text = "인원 확인 필요"
            else:
                count_text = f"저장 완료 {completed_students:,}명" if day_summary["has_history"] or completed_students else "기록 없음"
        day_rows.append(
            dedent(
                f"""
                <div class="log-history-strip" role="status" aria-label="{date_text} 로그 정리 {status_label}">
                  <span style="font-size:0.78rem; opacity:0.62; white-space:nowrap;">로그 정리</span>
                  <strong class="log-history-date">{date_text}</strong>
                  <strong class="log-history-status">{status_label}</strong>
                  <span class="log-history-time">{time_text}</span>
                  <span style="font-size:0.84rem; font-weight:500; margin-left:auto; white-space:nowrap;">{count_text}</span>
                </div>
                """
            ).strip()
        )
    return (placeholder if placeholder is not None else st).markdown(
        dedent(
            f"""
            <div class="log-history-stack" aria-label="최근 등원일 로그 정리">
              {''.join(day_rows)}
            </div>
            <style>
              .log-history-stack {{
                display:grid;
                gap:8px;
                width:100%;
                margin:0 0 12px 0;
              }}
              .log-history-strip {{
                display:flex;
                align-items:center;
                flex-wrap:wrap;
                gap:8px;
                width:100%;
                min-height:42px;
                box-sizing:border-box;
                margin:0;
                padding:8px 12px;
                border:1px solid rgba(128,128,128,0.28);
                border-radius:6px;
                background:rgba(128,128,128,0.035);
                line-height:1.25;
                letter-spacing:0;
              }}
              .log-history-date,
              .log-history-status {{
                font-size:0.9rem;
                font-weight:650;
                white-space:nowrap;
              }}
              .log-history-date {{
                padding-left:10px;
                border-left:1px solid rgba(128,128,128,0.28);
              }}
              .log-history-time {{
                font-size:0.84rem;
                opacity:0.68;
                white-space:nowrap;
              }}
            </style>
            """
        ).strip(),
        unsafe_allow_html=True,
    )


def refresh_log_csv_processing_history_summary(
    placeholder,
    history: LogCsvProcessingHistory,
) -> DeltaGenerator:
    return render_log_csv_processing_history_summary(history, placeholder=placeholder)


def load_current_log_csv_batch_state() -> LogCsvBatchState | None:
    return load_log_csv_batch_state(LOG_CSV_DOCS_STATE_PATH)


def persist_current_log_csv_batch_state(state: LogCsvBatchState) -> bool:
    try:
        save_log_csv_batch_state(LOG_CSV_DOCS_STATE_PATH, state)
    except (OSError, TypeError, ValueError) as exc:
        st.session_state["log_csv_to_docs_checkpoint_error"] = str(exc)
        return False
    st.session_state["log_csv_to_docs_checkpoint_error"] = ""
    return True


def release_stale_log_csv_session_lock() -> None:
    if csv_worker_is_running(LOG_CSV_WORKER_LOCK_PATH):
        st.session_state["log_csv_to_docs_is_saving"] = False
        return
    state = load_current_log_csv_batch_state()
    if state is not None and state["status"] == "running":
        try:
            with CsvRunLease(LOG_CSV_WORKER_LOCK_PATH):
                state = load_current_log_csv_batch_state()
                if state is not None and state["status"] == "running":
                    position = int(state.get("current_position") or 0) - 1
                    if (
                        not state.get("in_flight")
                        and state.get("phase") == "Google Docs 응답 대기 중"
                        and 0 <= position < len(state["selected_indices"])
                        and state["selected_indices"][position] not in state["completed_indices"]
                    ):
                        state["in_flight"] = {"source_index": state["selected_indices"][position]}
                    state = interrupt_log_csv_batch_state(
                        state, error="앱 실행이 종료되어 남은 작업을 복구했습니다.",
                        updated_at=log_csv_batch_now_iso(),
                    )
                    persist_current_log_csv_batch_state(state)
        except CsvRunBusyError:
            return
    session_is_saving = bool(st.session_state.get("log_csv_to_docs_is_saving", False))
    if should_release_log_csv_session_lock(
        load_current_log_csv_batch_state(),
        session_is_saving=session_is_saving,
        now=log_csv_batch_now_iso(),
    ):
        st.session_state["log_csv_to_docs_is_saving"] = False


def restore_log_csv_auto_import_from_batch_state() -> LogCsvBatchState | None:
    state = load_current_log_csv_batch_state()
    if state is None or st.session_state.get("log_csv_auto_import_path"):
        return state
    source_path_text = str(state.get("source_csv_path") or "").strip()
    if not source_path_text:
        return state
    source_path = Path(source_path_text)
    try:
        stat = source_path.stat()
        if (
            source_path.parent.resolve() != default_log_csv_downloads_dir().resolve()
            or not LOG_CSV_DOWNLOAD_NAME_PATTERN.fullmatch(source_path.name)
            or not source_path.is_file()
            or (
                int(state.get("source_mtime_ns") or 0) > 0
                and stat.st_mtime_ns != int(state.get("source_mtime_ns") or 0)
            )
        ):
            return state
    except OSError:
        return state
    token = log_csv_auto_export_token(source_path, stat.st_mtime_ns)
    st.session_state["log_csv_auto_import_path"] = str(source_path)
    st.session_state["log_csv_auto_import_mtime_ns"] = stat.st_mtime_ns
    st.session_state["log_csv_auto_export_started_token"] = token
    st.session_state["log_csv_auto_export_pending_token"] = ""
    return state


def rerun_log_csv_workspace() -> None:
    try:
        st.rerun(scope="fragment")
    except StreamlitInvalidLayoutContextError:
        st.rerun()


def handle_log_csv_preview_upload_change() -> None:
    st.session_state["log_csv_workspace_has_upload"] = bool(
        st.session_state.get("log_csv_to_docs_preview_upload_v2")
    )


def log_csv_completion_is_pending(state: LogCsvBatchState | None) -> bool:
    return bool(
        state is not None
        and state.get("status") == "completed"
        and state.get("results")
        and not str(state.get("acknowledged_at") or "").strip()
    )


def render_log_csv_recovered_completion(state: LogCsvBatchState) -> None:
    results = list(state.get("results") or [])
    groups = group_compact_log_csv_results(results)
    has_issue = bool(groups["needs_review"] or groups["failed"])
    saved_students = {key for _day, key in log_csv_confirmed_student_keys(results)}
    completed = int(state.get("completed_count") or 0)
    total = int(state.get("total") or 0)
    st.subheader("저장 결과")
    message = (
        f"이번 묶음 처리 완료 · {completed:,}/{total:,}건 · "
        f"저장 완료 {len(saved_students):,}명"
    )
    if has_issue:
        st.warning(message + " · 결과 확인 필요")
    else:
        st.success(message)
    st.caption(f"{state.get('source_csv_name') or 'CSV'} · 이번 작업 결과 · CSV 원본 미선택")
    if st.button(
        "이번 묶음 확인",
        type="primary",
        key=f"log_csv_to_docs_completion_confirmation_{state['run_id']}",
    ):
        acknowledged_state = acknowledge_log_csv_batch_state(
            state, acknowledged_at=log_csv_batch_now_iso(),
        )
        if persist_current_log_csv_batch_state(acknowledged_state):
            rerun_log_csv_workspace()
        else:
            st.error("완료 확인 상태를 저장하지 못했습니다. 다시 눌러 주세요.")
    render_compact_log_csv_result_list("확인 필요", groups["needs_review"])
    render_compact_log_csv_result_list("실패", groups["failed"])


@st.fragment(run_every=LOG_CSV_AUTO_IMPORT_POLL_INTERVAL)
def render_log_csv_persisted_progress(upload_signature_hash: str) -> None:
    if st.session_state.get("log_csv_to_docs_is_saving", False):
        return
    state = load_current_log_csv_batch_state()
    if not log_csv_batch_state_matches(state, upload_signature_hash):
        return
    total = max(0, int(state.get("total") or 0))
    completed = min(total, max(0, int(state.get("completed_count") or 0)))
    remaining = max(0, total - completed)
    percent = int((completed / total) * 100) if total else 0
    status = str(state.get("status") or "")
    recently_running = csv_worker_is_running(LOG_CSV_WORKER_LOCK_PATH)
    if recently_running:
        st.session_state["log_csv_observed_worker_run"] = state.get("run_id")
    elif st.session_state.pop("log_csv_observed_worker_run", None):
        st.rerun()
    st.progress(percent)
    if status == "completed":
        has_issue = any(
            classify_google_docs_save_result(result).get("count_key")
            in {"failed", "timeout_unknown", "lock_timeout_retryable", "needs_review", "duplicate_write_suspected"}
            for result in state.get("results", [])
        )
        message = f"이번 묶음 처리 완료 · {completed:,}/{total:,}건 처리"
        if has_issue:
            st.warning(message + " · 결과 확인 필요")
        else:
            st.success(message)
    elif recently_running:
        current_position = int(state.get("current_position") or 0)
        current_name = str(state.get("current_name") or "-")
        phase = str(state.get("phase") or "처리 중")
        st.info(
            f"Google Docs 저장 진행 중 · {completed:,}/{total:,}건 완료 · "
            f"현재 {current_position:,}/{total:,} · {current_name} · {phase}"
        )
    else:
        st.warning(
            f"Google Docs 저장이 중간에 멈춘 상태입니다 · "
            f"{completed:,}/{total:,}건 완료 · 남음 {remaining:,}건"
        )
    failed_count = len(state.get("failed_indices") or [])
    updated_at = str(state.get("updated_at") or "-")
    st.caption(f"처리 완료 {completed:,}건 · 남음 {remaining:,}건 · 문제 {failed_count:,}건 · 최근 상태 {updated_at}")
    if status == "interrupted" and str(state.get("error") or "").strip():
        st.caption(f"중단 원인: {short_preview_text(str(state.get('error') or ''), 180)}")


def should_auto_export_log_csv(
    *,
    current_token: str,
    pending_token: str,
    started_token: str,
    selected_count: int,
    has_web_app_url: bool,
    is_saving: bool,
) -> bool:
    return bool(
        current_token
        and current_token == pending_token
        and current_token != started_token
        and selected_count > 0
        and has_web_app_url
        and not is_saving
    )


def find_latest_completed_log_csv(
    downloads_dir: Path,
    *,
    newer_than_ns: int,
    now_ns: int,
) -> Path | None:
    if not downloads_dir.is_dir():
        return None

    latest_path: Path | None = None
    latest_mtime_ns = newer_than_ns
    for path in downloads_dir.glob("수업기록_*.csv"):
        if not LOG_CSV_DOWNLOAD_NAME_PATTERN.fullmatch(path.name):
            continue
        try:
            stat = path.stat()
            is_stable = now_ns - stat.st_mtime_ns >= LOG_CSV_AUTO_IMPORT_STABLE_NS
            if stat.st_size <= 0 or stat.st_mtime_ns <= newer_than_ns or not is_stable:
                continue
            with path.open("rb") as file:
                file.read(1)
        except OSError:
            continue
        if stat.st_mtime_ns > latest_mtime_ns:
            latest_path = path
            latest_mtime_ns = stat.st_mtime_ns
    return latest_path


def active_auto_import_log_csv() -> LocalLogCsvUpload | None:
    path_text = str(st.session_state.get("log_csv_auto_import_path") or "").strip()
    if not path_text:
        return None
    path = Path(path_text)
    try:
        if (
            path.parent.resolve() != default_log_csv_downloads_dir().resolve()
            or not LOG_CSV_DOWNLOAD_NAME_PATTERN.fullmatch(path.name)
            or not path.is_file()
        ):
            return None
    except OSError:
        return None
    return LocalLogCsvUpload(path)


@st.fragment(run_every=LOG_CSV_AUTO_IMPORT_POLL_INTERVAL)
def render_log_csv_auto_import_watcher() -> None:
    started_at_ns = int(
        st.session_state.setdefault("log_csv_auto_import_started_at_ns", time.time_ns())
    )
    if st.session_state.get("log_csv_to_docs_is_saving", False):
        return
    if csv_worker_is_running(LOG_CSV_WORKER_LOCK_PATH):
        return
    current_run_state = load_current_log_csv_batch_state()
    if log_csv_completion_is_pending(current_run_state):
        return
    if log_csv_batch_state_is_recently_running(
        current_run_state,
        now=log_csv_batch_now_iso(),
    ):
        return
    imported_mtime_ns = int(st.session_state.get("log_csv_auto_import_mtime_ns") or 0)
    candidate = find_latest_completed_log_csv(
        default_log_csv_downloads_dir(),
        newer_than_ns=imported_mtime_ns if imported_mtime_ns else started_at_ns,
        now_ns=time.time_ns(),
    )
    if candidate is None:
        return
    try:
        candidate_mtime_ns = candidate.stat().st_mtime_ns
    except OSError:
        return
    st.session_state["log_csv_auto_import_path"] = str(candidate)
    st.session_state["log_csv_auto_import_mtime_ns"] = candidate_mtime_ns
    st.session_state["log_csv_auto_export_pending_token"] = log_csv_auto_export_token(
        candidate,
        candidate_mtime_ns,
    )
    st.rerun()


def admin_consumed_filter_enabled() -> bool:
    return bool(st.session_state.get("admin_consumed_filter_enabled", True))


def _admin_consumed_note_preview(value: object, max_length: int = 60) -> str:
    preview = re.sub(r"\s+", " ", str(value or "").strip())
    if len(preview) <= max_length:
        return preview
    return preview[: max_length - 1] + "…"


def filter_consumed_admin_notes_from_log_rows(
    log_rows: list[dict[str, str]],
    *,
    enabled: bool,
) -> tuple[list[dict[str, str]], dict[str, object]]:
    filtered_rows: list[dict[str, str]] = []
    removed_rows: list[dict[str, object]] = []
    failed_open = False
    errors: list[str] = []
    registry = load_consumed_admin_notes() if enabled else None
    for index, row in enumerate(log_rows, start=1):
        row_copy = dict(row)
        original_admin_notes = row_copy.get("notes", "")
        row_copy["_admin_notes_original"] = str(original_admin_notes or "")
        try:
            result = filter_consumed_admin_notes(
                row_copy.get("name"),
                row_copy.get("admin_sid") or row_copy.get("sid"),
                original_admin_notes,
                enabled=enabled,
                registry=registry,
                counseling_date=row_copy.get("date") or row_copy.get("counseling_date"),
            )
            filtered_notes = result.get("filtered_notes", row_copy.get("notes", ""))
            row_copy["notes"] = str(filtered_notes or "")
            removed_notes = [str(note) for note in result.get("removed_notes", [])]
            diagnostic = result.get("diagnostic") or {}
            failed_open = failed_open or bool(diagnostic.get("failed_open"))
            if diagnostic.get("error"):
                errors.append(str(diagnostic.get("error")))
            if removed_notes:
                row_copy["_consumed_admin_notes_removed_count"] = str(len(removed_notes))
                row_copy["_consumed_admin_notes_removed_preview"] = " | ".join(
                    _admin_consumed_note_preview(note) for note in removed_notes[:3]
                )
                row_copy["_consumed_admin_notes_removed_values"] = "\n".join(removed_notes)
                removed_rows.append(
                    {
                        "row_index": index,
                        "name": row_copy.get("name", ""),
                        "date": row_copy.get("date", ""),
                        "removed_count": len(removed_notes),
                        "preview": row_copy["_consumed_admin_notes_removed_preview"],
                    }
                )
        except Exception as exc:
            failed_open = True
            errors.append(str(exc))
        filtered_rows.append(row_copy)
    return filtered_rows, {
        "enabled": enabled,
        "removed_count": sum(int(row.get("removed_count") or 0) for row in removed_rows),
        "removed_rows": removed_rows,
        "failed_open": failed_open,
        "errors": errors[:5],
    }


def mark_consumed_admin_notes_for_saved_row(
    row: dict[str, str],
    result_entry: dict[str, object],
    final_text: str,
) -> dict[str, object]:
    try:
        result_status = str(result_entry.get("result_status") or result_entry.get("result_label") or "")
        review_category = str(result_entry.get("review_category") or "")
        if (
            review_category == "unsafe_similar_record"
            or is_truthy_result_value(result_entry.get("duplicate_write_suspected"))
        ):
            consumed_status = "unsafe_similar_record"
        elif is_truthy_result_value(result_entry.get("updated_existing")):
            consumed_status = "updated_existing"
        elif result_status == "saved_confirmed_after_timeout":
            consumed_status = "saved_confirmed_after_timeout"
        elif is_truthy_result_value(result_entry.get("duplicate_skipped")):
            consumed_status = "duplicate_skipped"
        elif is_truthy_result_value(result_entry.get("needs_review")):
            consumed_status = "needs_review"
        elif is_truthy_result_value(result_entry.get("newly_added")):
            consumed_status = "added"
        else:
            consumed_status = result_status
        notes_to_mark = []
        for value in (
            row.get("notes", ""),
            *extract_consumable_admin_section_notes_from_docs_text(final_text),
        ):
            clean_value = str(value or "").strip()
            if clean_value and clean_value not in notes_to_mark:
                notes_to_mark.append(clean_value)
        return mark_admin_notes_consumed(
            row.get("name"),
            row.get("admin_sid") or row.get("sid"),
            notes_to_mark,
            consumed_status,
            final_text=final_text,
            counseling_date=row.get("date") or row.get("counseling_date"),
        )
    except Exception as exc:
        return {"ok": False, "marked_count": 0, "error": str(exc)}


def render_consumed_admin_notes_diagnostics(ui_locked: bool) -> None:
    with st.expander("ADMIN consumed 필터", expanded=False):
        enabled = st.checkbox(
            "ADMIN consumed 필터 적용",
            value=admin_consumed_filter_enabled(),
            key="admin_consumed_filter_enabled",
            disabled=ui_locked,
            help="Google Docs에 성공적으로 반영된 ADMIN 특이사항을 이후 CSV/미리보기/정리본 context에서 숨깁니다.",
        )
        registry = load_consumed_admin_notes()
        diagnostic = diagnose_consumed_admin_notes()
        items = list(registry.get("items") or [])
        st.write(f"- 상태: `{'ON' if enabled else 'OFF'}`")
        st.write(f"- registry 경로: `{CONSUMED_ADMIN_NOTES_PATH}`")
        st.write(f"- registry 파일: `{'있음' if diagnostic.get('exists') else '없음'}`")
        st.write(f"- registry 로드: `{'정상' if diagnostic.get('load_ok') else '문제 있음'}`")
        st.write(f"- consumed 항목: `{int(diagnostic.get('item_count') or len(items)):,}`")
        if diagnostic.get("duplicate_count"):
            st.write(f"- 중복 항목 후보: `{int(diagnostic.get('duplicate_count') or 0):,}`")
        if diagnostic.get("missing_required_count"):
            st.write(f"- 필수 필드 누락 항목: `{int(diagnostic.get('missing_required_count') or 0):,}`")
        if registry.get("load_error") or diagnostic.get("load_error"):
            st.warning("registry를 읽는 중 문제가 있어 fail-open으로 동작합니다.")
            if diagnostic.get("load_error"):
                st.caption(f"load_error: {diagnostic.get('load_error')}")
            if registry.get("load_error"):
                st.caption(f"fail-open: {registry.get('load_error')}")
        backup_files = list(diagnostic.get("backup_files") or [])
        if backup_files:
            st.caption(f"최근 backup/corrupt 파일: {len(backup_files)}개")
        preview_rows = list(diagnostic.get("recent_items") or [])
        if not preview_rows and items:
            preview_rows = [
                {
                    "consumed_at": item.get("consumed_at", ""),
                    "source": item.get("source", ""),
                    "save_status": item.get("save_status", ""),
                    "note_preview": _admin_consumed_note_preview(item.get("note_preview", ""), 60),
                }
                for item in items[-10:]
            ]
        if preview_rows:
            st.dataframe(preview_rows, use_container_width=True, hide_index=True)
        if st.button("ADMIN consumed registry 초기화", disabled=ui_locked, key="reset_admin_consumed_registry"):
            result = reset_consumed_admin_notes()
            if result.get("ok"):
                backup_path = result.get("backup_path") or ""
                if backup_path:
                    st.success(f"ADMIN consumed registry를 백업 후 초기화했습니다. backup: {backup_path}")
                else:
                    st.success("ADMIN consumed registry를 초기화했습니다.")
                st.rerun()
            else:
                st.warning(f"초기화에 실패했습니다: {result.get('error', '-')}")










def render_app_health_diagnostics(ui_locked: bool) -> None:
    with st.expander("운영 진단", expanded=False):
        st.caption("실제 Google Docs 저장/admin 쓰기 없이 설정과 필수 파일만 확인합니다.")
        if st.button("운영 진단 새로고침", disabled=ui_locked, key="refresh_app_health_diagnostics"):
            st.session_state.pop("app_health_diagnostics", None)
        if "app_health_diagnostics" not in st.session_state:
            try:
                from scripts.check_app_health import run_health_checks

                st.session_state["app_health_diagnostics"] = [
                    item.to_dict() for item in run_health_checks(BASE_DIR, include_slow=False)
                ]
            except Exception as exc:
                st.session_state["app_health_diagnostics"] = [
                    {
                        "status": "WARN",
                        "name": "health_check",
                        "description": f"운영 진단을 실행하지 못했습니다: {exc}",
                        "action": "scripts/check_app_health.py를 콘솔에서 실행해 주세요.",
                    }
                ]
        rows = list(st.session_state.get("app_health_diagnostics") or [])
        pass_count = sum(1 for row in rows if row.get("status") == "PASS")
        warn_count = sum(1 for row in rows if row.get("status") == "WARN")
        fail_count = sum(1 for row in rows if row.get("status") == "FAIL")
        runtime_info = runtime_environment_diagnostics()
        build_info_summary = runtime_info.get("build_info") or {}
        if not runtime_info.get("is_expected_runtime_path"):
            st.warning(
                "현재 앱이 기준 작업 폴더가 아닌 다른 설치본에서 실행 중입니다. "
                "최신 수정이 반영되지 않았을 수 있습니다."
            )
        st.write(f"- 현재 실행 경로: `{runtime_info.get('current_runtime_path')}`")
        st.write(f"- app.py 경로: `{runtime_info.get('app_py_path')}`")
        st.write(f"- 기준 실사용 경로: `{runtime_info.get('expected_runtime_path')}`")
        st.write(f"- APP_VERSION.txt: `{runtime_info.get('app_version')}`")
        st.write(f"- BUILD_INFO channel: `{build_info_summary.get('channel') or '-'}`")
        st.write(f"- BUILD_INFO package_type: `{build_info_summary.get('package_type') or '-'}`")
        st.write(f"- BUILD_INFO version: `{build_info_summary.get('version') or '-'}`")
        st.write(f"- BUILD_INFO build_time: `{build_info_summary.get('build_time') or '-'}`")
        st.write(f"- 앱 버전: `{read_app_version()}`")
        st.write(f"- {format_build_info_badge()}")
        build_info = read_build_info()
        if build_info:
            st.json(build_info)
        else:
            st.caption("BUILD_INFO.json이 없거나 읽을 수 없습니다.")
        st.write(f"- 요약: PASS `{pass_count}` / WARN `{warn_count}` / FAIL `{fail_count}`")
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)


def unique_google_docs_append_log_path(timestamp: str) -> Path:
    GOOGLE_DOCS_APPEND_LOG_DIR.mkdir(parents=True, exist_ok=True)
    base_path = GOOGLE_DOCS_APPEND_LOG_DIR / f"log_csv_to_docs_result_{timestamp}.csv"
    if not base_path.exists():
        return base_path

    counter = 2
    while True:
        candidate = GOOGLE_DOCS_APPEND_LOG_DIR / f"log_csv_to_docs_result_{timestamp}_{counter}.csv"
        if not candidate.exists():
            return candidate
        counter += 1


def save_log_csv_to_docs_result_log(
    results: list[dict[str, object]],
    source_csv_name: str,
    source_csv_size: int | str,
    *,
    run_id: str = "",
) -> Path:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    result_run_id = str(run_id or "").strip() or f"log_csv_to_docs_{timestamp}"
    saved_at = now.isoformat(timespec="seconds")
    log_path = unique_google_docs_append_log_path(timestamp)
    fieldnames = [
        "run_id",
        "saved_at",
        "source_csv_name",
        "source_csv_size",
        "retry_of_run_id",
        "retry_attempt",
        "retry_source_status",
        "source_index",
        "batch_index",
        "batch_size",
        "batch_total",
        "date",
        "name",
        "sid",
        "school",
        "curriculum",
        "admin_sid",
        "matched_subject",
        "matched_class_type",
        "match_confidence",
        "match_reason",
        "match_needs_review",
        "candidate_count",
        "doc_id",
        "doc_url",
        "dedupe_key",
        "duplicate_reason",
        "dedupe_key_stale",
        "result_status",
        "result_message",
        "row",
        "col",
        "added_row",
        "copied_row_template",
        "style_template_used",
        "style_template_source",
        "max_cell_count",
        "needs_review",
        "review_reason",
        "timeout_unknown",
        "retry_recommended",
        "review_category",
        "updated_existing",
        "already_saved_no_write",
        "duplicate_skipped",
        "duplicate_write_suspected",
        "skipped_no_write",
        "newly_added",
        "result_label",
        "manual_cleanup_recommended",
        "admin_consumed_marked_count",
        "admin_consumed_mark_error",
        "summary_preview",
        "summary_length",
    ]

    with log_path.open("x", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            summary_preview = str(result.get("summary_preview") or "")
            writer.writerow(
                {
                    "run_id": result_run_id,
                    "saved_at": saved_at,
                    "source_csv_name": source_csv_name,
                    "source_csv_size": source_csv_size,
                    "retry_of_run_id": result.get("retry_of_run_id", ""),
                    "retry_attempt": result.get("retry_attempt", ""),
                    "retry_source_status": result.get("retry_source_status", ""),
                    "source_index": result.get("source_index", ""),
                    "batch_index": result.get("batch_index", ""),
                    "batch_size": result.get("batch_size", ""),
                    "batch_total": result.get("batch_total", ""),
                    "date": result.get("date", ""),
                    "name": result.get("name", ""),
                    "sid": result.get("sid", ""),
                    "school": result.get("school", ""),
                    "curriculum": result.get("curriculum", ""),
                    "admin_sid": result.get("admin_sid", ""),
                    "matched_subject": result.get("matched_subject", ""),
                    "matched_class_type": result.get("matched_class_type", ""),
                    "match_confidence": result.get("match_confidence", ""),
                    "match_reason": result.get("match_reason", ""),
                    "match_needs_review": result.get("match_needs_review", ""),
                    "candidate_count": result.get("candidate_count", ""),
                    "doc_id": result.get("doc_id", ""),
                    "doc_url": result.get("doc_url", ""),
                    "dedupe_key": result.get("dedupe_key", ""),
                    "duplicate_reason": result.get("duplicate_reason", ""),
                    "dedupe_key_stale": result.get("dedupe_key_stale", ""),
                    "result_status": result.get("result_status", result.get("寃곌낵", "")),
                    "result_message": result.get("result_message", result.get("硫붿떆吏", "")),
                    "row": result.get("row", ""),
                    "col": result.get("col", ""),
                    "added_row": result.get("added_row", ""),
                    "copied_row_template": result.get("copied_row_template", ""),
                    "style_template_used": result.get("style_template_used", ""),
                    "style_template_source": result.get("style_template_source", ""),
                    "max_cell_count": result.get("max_cell_count", ""),
                    "needs_review": result.get("needs_review", ""),
                    "review_reason": result.get("review_reason", ""),
                    "timeout_unknown": result.get("timeout_unknown", ""),
                    "retry_recommended": result.get("retry_recommended", ""),
                    "review_category": result.get("review_category", ""),
                    "updated_existing": result.get("updated_existing", ""),
                    "already_saved_no_write": result.get("already_saved_no_write", ""),
                    "duplicate_skipped": result.get("duplicate_skipped", ""),
                    "duplicate_write_suspected": result.get("duplicate_write_suspected", ""),
                    "skipped_no_write": result.get("skipped_no_write", ""),
                    "newly_added": result.get("newly_added", ""),
                    "result_label": result.get("result_label", ""),
                    "manual_cleanup_recommended": result.get("manual_cleanup_recommended", ""),
                    "admin_consumed_marked_count": result.get("admin_consumed_marked_count", ""),
                    "admin_consumed_mark_error": result.get("admin_consumed_mark_error", ""),
                    "summary_preview": summary_preview[:300],
                    "summary_length": result.get("summary_length", ""),
                }
            )

    return log_path


def build_log_csv_to_docs_result_entry(
    row: dict[str, str],
    status: str,
    message: str,
    doc_url: str = "",
    doc_id: str = "",
    dedupe_key: str = "",
    duplicate_reason: object = "",
    dedupe_key_stale: object = "",
    response_row: object = "",
    response_col: object = "",
    added_row: object = "",
    copied_row_template: object = "",
    style_template_used: object = "",
    style_template_source: object = "",
    max_cell_count: object = "",
    needs_review: object = "",
    review_reason: object = "",
    timeout_unknown: object = "",
    retry_recommended: object = "",
    review_category: object = "",
    updated_existing: object = "",
    already_saved_no_write: object = "",
    duplicate_skipped: object = "",
    duplicate_write_suspected: object = "",
    skipped_no_write: object = "",
    newly_added: object = "",
    result_label: object = "",
    manual_cleanup_recommended: object = "",
    retry_of_run_id: object = "",
    retry_attempt: object = "",
    retry_source_status: object = "",
    source_index: object = "",
    batch_index: object = "",
    batch_size: object = "",
    batch_total: object = "",
    admin_sid: object = "",
    matched_subject: object = "",
    matched_class_type: object = "",
    match_confidence: object = "",
    match_reason: object = "",
    match_needs_review: object = "",
    candidate_count: object = "",
    summary_text: str = "",
) -> dict[str, object]:
    summary_text = str(summary_text or "")
    return {
        "date": str(row.get("date") or ""),
        "name": str(row.get("name") or "").strip(),
        "sid": str(row.get("sid") or ""),
        "school": str(row.get("school") or ""),
        "curriculum": str(row.get("curriculum") or ""),
        "admin_sid": admin_sid,
        "matched_subject": matched_subject,
        "matched_class_type": matched_class_type,
        "match_confidence": match_confidence,
        "match_reason": match_reason,
        "match_needs_review": match_needs_review,
        "candidate_count": candidate_count,
        "source_index": source_index,
        "retry_of_run_id": retry_of_run_id,
        "retry_attempt": retry_attempt,
        "retry_source_status": retry_source_status,
        "batch_index": batch_index,
        "batch_size": batch_size,
        "batch_total": batch_total,
        "寃곌낵": status,
        "硫붿떆吏": message,
        "doc_url": doc_url,
        "doc_id": doc_id,
        "dedupe_key": dedupe_key,
        "duplicate_reason": duplicate_reason,
        "dedupe_key_stale": dedupe_key_stale,
        "result_status": status,
        "result_message": message,
        "row": response_row,
        "col": response_col,
        "added_row": added_row,
        "copied_row_template": copied_row_template,
        "style_template_used": style_template_used,
        "style_template_source": style_template_source,
        "max_cell_count": max_cell_count,
        "needs_review": needs_review,
        "review_reason": review_reason,
        "timeout_unknown": timeout_unknown,
        "retry_recommended": retry_recommended,
        "review_category": review_category,
        "updated_existing": updated_existing,
        "already_saved_no_write": already_saved_no_write,
        "duplicate_skipped": duplicate_skipped,
        "duplicate_write_suspected": duplicate_write_suspected,
        "skipped_no_write": skipped_no_write,
        "newly_added": newly_added,
        "result_label": result_label or status,
        "manual_cleanup_recommended": manual_cleanup_recommended,
        "summary_duplicate_fingerprint": build_log_csv_duplicate_summary_fingerprint(summary_text),
        "summary_preview": summary_text[:300],
        "summary_length": len(summary_text),
    }


def log_csv_admin_match_log_fields(item: dict[str, object]) -> dict[str, object]:
    applied_subject_info = item.get("applied_subject_info") or {}
    admin_subject_info = item.get("admin_subject_info") or {}
    docs_matches = item.get("docs_matches") or []
    admin_sid = ""
    if len(docs_matches) == 1:
        admin_sid = str(docs_matches[0].get("admin_sid") or "")
    return {
        "admin_sid": admin_sid,
        "matched_subject": applied_subject_info.get("applied_subject") or "",
        "matched_class_type": applied_subject_info.get("matched_class_type") or "",
        "match_confidence": applied_subject_info.get("match_confidence") or "",
        "match_reason": applied_subject_info.get("match_reason") or admin_subject_info.get("reason") or "",
        "match_needs_review": applied_subject_info.get("match_needs_review") or False,
        "candidate_count": applied_subject_info.get("admin_candidate_count") or admin_subject_info.get("candidate_count") or "",
    }


def is_truthy_result_value(value: object) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def is_false_result_value(value: object) -> bool:
    if value is False:
        return True
    if value is True or value is None:
        return False
    return str(value).strip().lower() in {"0", "false", "no", "n"}


def google_docs_style_source_needs_review(style_template_source: object) -> bool:
    source = str(style_template_source or "").strip()
    return source in {
        "detail_blue_cell",
        "header_detail_blue_cell",
        "existing_detail_cell_fallback",
        "first_detail_cell",
        "last_cell",
    }


def google_docs_result_is_terminal_issue(result: dict[str, object]) -> bool:
    status = str(result.get("result_status") or result.get("결과") or "").strip().lower()
    return (
        is_truthy_result_value(result.get("timeout_unknown"))
        or status in {"timeout_unknown", "lock_timeout_retryable", "failed_retryable", "failed", "failure"}
        or "실패" in status
        or str(result.get("review_category") or "").strip() in {"lock_timeout", "lock_timeout_retryable"}
    )


def google_docs_review_reason_needs_review(review_reason: object) -> bool:
    text = str(review_reason or "").strip().lower()
    if not text:
        return False
    return any(
        keyword in text
        for keyword in [
            "fallback",
            "서식 fallback",
            "서식 복사 실패",
            "복사 실패",
            "행/칸 위치 확인",
            "위치 확인",
            "서식 확인 필요",
        ]
    )


def google_docs_added_row_style_success(result: dict[str, object]) -> bool:
    if not is_truthy_result_value(result.get("added_row")):
        return False
    message = str(result.get("message") or "")
    if result.get("duplicate_skipped") is True or message.startswith("duplicate_skipped"):
        return False
    if google_docs_result_is_terminal_issue(result):
        return False
    if is_truthy_result_value(result.get("dedupe_key_stale", False)):
        return False
    return (
        is_truthy_result_value(result.get("copied_row_template"))
        and is_truthy_result_value(result.get("style_template_used"))
        and bool(str(result.get("style_template_source") or "").strip())
        and not google_docs_style_source_needs_review(result.get("style_template_source", ""))
        and not google_docs_review_reason_needs_review(result.get("review_reason"))
    )


def add_google_docs_review_reason(existing_reason: str, reason: str) -> str:
    reasons = [part.strip() for part in str(existing_reason or "").split(";") if part.strip()]
    if reason and reason not in reasons:
        reasons.append(reason)
    return "; ".join(reasons)


def google_docs_response_needs_review(result: dict[str, object]) -> bool:
    message = str(result.get("message") or "")
    if result.get("duplicate_skipped") is True or message.startswith("duplicate_skipped"):
        return False
    if google_docs_added_row_style_success(result):
        return False
    added_row = result.get("added_row", "")
    return (
        is_truthy_result_value(result.get("needs_review", False))
        or is_truthy_result_value(result.get("dedupe_key_stale", False))
        or google_docs_review_reason_needs_review(result.get("review_reason"))
        or (is_truthy_result_value(added_row) and is_false_result_value(result.get("copied_row_template", "")))
        or (is_truthy_result_value(added_row) and not str(result.get("copied_row_template") or "").strip())
        or is_false_result_value(result.get("style_template_used", ""))
        or (is_truthy_result_value(added_row) and not str(result.get("style_template_used") or "").strip())
        or (is_truthy_result_value(added_row) and not str(result.get("style_template_source") or "").strip())
        or google_docs_style_source_needs_review(result.get("style_template_source", ""))
    )


def google_docs_response_review_reason(result: dict[str, object]) -> str:
    review_reason = str(result.get("review_reason") or "")
    if is_truthy_result_value(result.get("dedupe_key_stale", False)):
        review_reason = add_google_docs_review_reason(
            review_reason,
            "dedupe_key는 있었지만 문서에서 동일 내용을 찾지 못해 새로 추가했습니다.",
        )
    added_row = result.get("added_row", "")
    if is_truthy_result_value(added_row) and not google_docs_added_row_style_success(result):
        review_reason = add_google_docs_review_reason(
            review_reason,
            "새 행이 생성되었습니다. 위 행과 양식이 같은지 확인하세요.",
        )
    if is_truthy_result_value(added_row) and is_false_result_value(result.get("copied_row_template", "")):
        review_reason = add_google_docs_review_reason(
            review_reason,
            "행 템플릿 복사에 실패해 fallback 행을 사용했습니다.",
        )
    if google_docs_style_source_needs_review(result.get("style_template_source", "")):
        review_reason = add_google_docs_review_reason(
            review_reason,
            "상담 내용 칸 서식을 찾지 못해 fallback 서식을 사용했습니다.",
        )
    if is_false_result_value(result.get("style_template_used", "")):
        review_reason = add_google_docs_review_reason(
            review_reason,
            "상담 내용 칸 서식을 적용하지 못했습니다.",
        )
    return review_reason


def result_run_id_from_log_path(log_path: str | Path | None) -> str:
    if not log_path:
        return ""
    stem = Path(str(log_path)).stem
    if stem.startswith("log_csv_to_docs_result_"):
        return "log_csv_to_docs_" + stem.removeprefix("log_csv_to_docs_result_")
    return stem


def is_log_csv_retry_target_result(result: dict[str, object]) -> bool:
    classification = classify_google_docs_save_result(result)
    return bool(classification.get("retry_allowed"))


def log_csv_result_has_timeout_or_lock_message(result: dict[str, object]) -> bool:
    text = " ".join(
        str(result.get(field) or "")
        for field in (
            "result_status",
            "result_message",
            "review_reason",
            "review_category",
            "결과",
            "메시지",
        )
    ).lower()
    return any(
        keyword in text
        for keyword in (
            "timeout",
            "시간초과",
            "시간 초과",
            "lock",
            "lockservice",
            "잠금",
            "다른 프로세스",
        )
    )


def is_log_csv_retry_or_review_search_result(result: dict[str, object]) -> bool:
    classification = classify_google_docs_save_result(result)
    return bool(classification.get("show_in_compact_list")) or is_truthy_result_value(
        result.get("retry_recommended")
    )


def log_csv_duplicate_reason_label(result: dict[str, object]) -> str:
    reason = str(result.get("duplicate_reason") or "").strip().lower()
    message = str(result.get("result_message") or result.get("메시지") or "").strip().lower()
    if reason == "unsafe_similar_record" or message == "similar_record_needs_review":
        return "유사 기록 확인 필요"
    if reason == "normalized_content" or "duplicate_skipped_by_normalized_content" in message:
        return "포맷 차이 감지"
    if reason == "content" or "duplicate_skipped_by_content" in message:
        return "문서 안 같은 내용 확인"
    if reason == "key" or "duplicate_skipped_by_key" in message:
        return "같은 저장 키 확인"
    if reason:
        return f"중복 방지 확인: {reason}"
    return "기존 Google Docs 기록 확인"


def log_csv_result_is_already_saved(result: dict[str, object]) -> bool:
    return classify_google_docs_save_result(result).get("count_key") == "already_saved"


def log_csv_result_is_unsafe_similar(result: dict[str, object]) -> bool:
    reason = str(result.get("duplicate_reason") or "").strip().lower()
    message = str(result.get("result_message") or result.get("메시지") or "").strip().lower()
    review_category = str(result.get("review_category") or "").strip()
    return reason == "unsafe_similar_record" or review_category == "unsafe_similar_record" or message == "similar_record_needs_review"


def log_csv_result_is_failure(result: dict[str, object]) -> bool:
    return bool(classify_google_docs_save_result(result).get("is_failure_like"))


def compact_log_csv_result_status(result: dict[str, object]) -> str:
    return str(classify_google_docs_save_result(result).get("label") or "신규 저장")


def compact_log_csv_result_group(result: dict[str, object]) -> str:
    classification = classify_google_docs_save_result(result)
    group = str(classification.get("group") or "")
    if group == "failed":
        return "failed"
    if group == "needs_check":
        return "needs_review"
    if group == "already_processed":
        return "already_processed"
    return "newly_added"


def group_compact_log_csv_results(
    results: list[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    grouped = {
        "already_processed": [],
        "needs_review": [],
        "failed": [],
        "newly_added": [],
    }
    for result in results:
        classification = classify_google_docs_save_result(result)
        if not classification.get("show_in_compact_list"):
            grouped["newly_added"].append(result)
            continue
        grouped[compact_log_csv_result_group(result)].append(result)
    return grouped


def google_docs_url_from_fields(doc_url: object = "", doc_id: object = "") -> str:
    raw_url = str(doc_url or "").strip()
    raw_doc_id = str(doc_id or "").strip()
    if raw_url and "docs.google.com/document/d/" in raw_url:
        return raw_url
    if raw_url.startswith(("http://", "https://")) and "docs.google.com" not in raw_url:
        return normalize_google_docs_doc_url("", raw_doc_id) if raw_doc_id else ""
    extracted_doc_id = extract_google_docs_doc_id(raw_url) if raw_url else ""
    final_doc_id = raw_doc_id or extracted_doc_id
    if final_doc_id:
        return normalize_google_docs_doc_url("", final_doc_id)
    return ""


def render_compact_docs_link_line(text: str, doc_url: str, key: str) -> None:
    if doc_url:
        st.markdown(f"- {text} · [Docs 열기]({doc_url})")
    else:
        st.markdown(f"- {text} · Docs 링크 없음")


def compact_log_csv_result_text(result: dict[str, object]) -> str:
    name = str(result.get("name") or "-").strip() or "-"
    return f"{name} · {compact_log_csv_result_status(result)}"


def render_compact_log_csv_result_list(
    title: str,
    results: list[dict[str, object]],
) -> None:
    if not results:
        return
    with st.expander(f"{title} {len(results):,}건", expanded=False):
        for row_index, result in enumerate(results):
            doc_url = google_docs_url_from_fields(result.get("doc_url"), result.get("doc_id"))
            render_compact_docs_link_line(
                compact_log_csv_result_text(result),
                doc_url,
                f"{title}_docs_link_{row_index}_{abs(hash(str(result)))}",
            )


def log_csv_save_excluded_reason(item: dict[str, object]) -> str:
    row = item.get("source_row") or {}
    status = str(item.get("status") or "").strip()
    docs_preview_text = str(item.get("docs_preview_text") or "").strip()

    if not str(row.get("name") or "").strip():
        return "학생명 없음"
    if not str(row.get("date") or "").strip():
        return "상담일 없음"
    if status == "Docs 매핑 없음":
        return "학생 Docs 매핑 없음"
    if status == "Docs 매핑 중복":
        return "학생 Docs 매핑 중복"
    if status == "기록 내용 없음":
        if not docs_preview_text and log_row_has_docs_content(row):
            return "Google Docs 전송용 본문 없음"
        return "기록 내용 없음"
    if not docs_preview_text:
        return "Google Docs 전송용 본문 없음"
    return status or "저장 제외 사유 확인 필요"


def log_csv_save_excluded_doc_url(item: dict[str, object]) -> str:
    docs_matches = item.get("docs_matches") or []
    if len(docs_matches) != 1:
        return ""
    doc_match = docs_matches[0]
    return google_docs_url_from_fields(doc_match.get("doc_url"), doc_match.get("doc_id"))


def compact_log_csv_save_excluded_text(item: dict[str, object]) -> str:
    row = item.get("source_row") or {}
    row_number = str(item.get("row_index") or "-").strip() or "-"
    name = str(row.get("name") or "-").strip() or "-"
    row_date = str(row.get("date") or "-").strip() or "-"
    reason = log_csv_save_excluded_reason(item)
    return f"{row_number}행 · {name} · {row_date} · {reason} · 저장 안 됨"


def render_log_csv_save_excluded_list(
    excluded_items: list[dict[str, object]],
    *,
    max_items: int = 20,
) -> None:
    if not excluded_items:
        return
    with st.expander(f"저장 제외 항목 {len(excluded_items):,}건", expanded=True):
        st.caption("아래 항목은 현재 선택 목록에 포함되지 않아 Google Docs로 전송되지 않습니다.")
        for row_index, item in enumerate(excluded_items[:max_items]):
            doc_url = log_csv_save_excluded_doc_url(item)
            render_compact_docs_link_line(
                compact_log_csv_save_excluded_text(item),
                doc_url,
                f"save_excluded_docs_link_{row_index}_{abs(hash(str(item)))}",
            )
        if len(excluded_items) > max_items:
            st.caption(f"외 {len(excluded_items) - max_items:,}건은 개발자 상세 진단에서 확인하세요.")


def parse_log_export_date(value: str) -> date:
    text = (value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise WorkflowStepError("로그 CSV → 학생 Docs 추가", f"날짜 형식이 올바르지 않습니다: {text}") from exc


def log_csv_source_date_from_rows(rows: list[dict[str, str]]) -> str:
    source_dates: set[str] = set()
    for row in rows:
        date_text = str(row.get("date") or "").strip()
        if not date_text:
            continue
        try:
            source_dates.add(parse_log_export_date(date_text).isoformat())
        except WorkflowStepError:
            continue
    return next(iter(source_dates)) if len(source_dates) == 1 else ""


LOG_CSV_SPECIAL_CLASS_COLUMNS = (
    "수업구분",
    "강의구분",
    "curriculum",
    "course",
    "type",
    "category",
    "구분",
    "class",
    "class_name",
    "className",
)


LOG_CSV_LESSON_TYPE_COLUMNS = (
    "class_type",
    "lesson_type",
    "course_type",
    "수업유형",
    "강의유형",
    "수업구분",
    "강의구분",
    "구분",
)


LOG_CSV_REGULAR_LESSON_KEYWORDS = (
    "정규",
    "정규강의",
    "일반",
    "본수업",
)


LOG_CSV_SPECIAL_LESSON_KEYWORDS = (
    "특강",
    "특강반",
    "방학특강",
    "내신특강",
    "보충특강",
)


LOG_CSV_TEXTBOOK_SPECIAL_KEYWORDS = (
    "수능특강",
    "수능 특강",
    "EBS 수능특강",
    "EBS 수능 특강",
    "EBS수능특강",
    "수특",
)


LOG_CSV_EXPLICIT_SUBJECT_COLUMNS = (
    "최종 적용 과목",
    "적용 과목",
    "CSV 과목",
    "과목",
    "final_subject",
    "applied_subject",
    "selected_subject",
    "matched_subject",
    "subject",
    "course",
    "class",
    "class_name",
    "className",
)


LOG_CSV_SUBJECT_COLUMNS = (
    *LOG_CSV_EXPLICIT_SUBJECT_COLUMNS,
    "curriculum",
    "subCurriculum",
    "program",
    "group",
    "type",
    "category",
    "수업구분",
    "강의구분",
    "구분",
)


ADMIN_STUDENT_SUBJECT_FIELDS = (
    "curriculum",
    "subCurriculum",
    "course",
    "subject",
    "class",
    "class_name",
    "className",
    "program",
    "group",
)


ADMIN_STUDENT_LESSON_TYPE_FIELDS = LOG_CSV_LESSON_TYPE_COLUMNS


ADMIN_STUDENT_SCHEDULE_FIELDS = (
    "day",
    "day1",
    "day2",
    "day3",
    "day4",
    "day5",
    "schedule",
    "time",
    "visitTime1",
    "visitTime2",
    "visitTime3",
    "visitTime4",
    "visitTime5",
    "start_time",
    "startTime",
    "class",
    "class_name",
    "className",
)

ADMIN_STUDENT_INDEXED_SCHEDULE_PAIRS = (
    ("day1", "visitTime1", "day1"),
    ("day2", "visitTime2", "day2"),
    ("day3", "visitTime3", "day3"),
    ("day4", "visitTime4", "day4"),
    ("day5", "visitTime5", "day5"),
)

ADMIN_STUDENT_ACTUAL_DATE_FIELDS = (
    "date",
    "counseling_date",
    "lesson_date",
    "class_date",
    "log_date",
    "수업일",
    "상담일",
    "날짜",
)


ADMIN_STUDENT_MATCH_FIELDS = (
    *ADMIN_STUDENT_SUBJECT_FIELDS,
    *ADMIN_STUDENT_LESSON_TYPE_FIELDS,
    "level",
    *ADMIN_STUDENT_SCHEDULE_FIELDS,
    *ADMIN_STUDENT_ACTUAL_DATE_FIELDS,
    "teacher",
)


LOG_CSV_WEEKDAY_LABELS = ("월", "화", "수", "목", "금", "토", "일")

LOG_CSV_DATE_COLUMNS = (
    "date",
    "counseling_date",
    "lesson_date",
    "class_date",
    "수업일",
    "상담일",
    "날짜",
)

LOG_CSV_WEEKDAY_COLUMNS = (
    "weekday",
    "lesson_weekday",
    "class_weekday",
    "day",
    "수업요일",
    "요일",
)

LOG_CSV_ENGLISH_WEEKDAY_LABELS = {
    "mon": "월",
    "monday": "월",
    "m": "월",
    "tue": "화",
    "tues": "화",
    "tuesday": "화",
    "tu": "화",
    "t": "화",
    "wed": "수",
    "wednesday": "수",
    "we": "수",
    "w": "수",
    "thu": "목",
    "thur": "목",
    "thurs": "목",
    "thursday": "목",
    "th": "목",
    "r": "목",
    "fri": "금",
    "friday": "금",
    "fr": "금",
    "f": "금",
    "sat": "토",
    "saturday": "토",
    "sa": "토",
    "s": "토",
    "sun": "일",
    "sunday": "일",
    "su": "일",
    "u": "일",
}


LOG_CSV_SPECIAL_CONTENT_COLUMNS = (
    "notes",
    "memo",
    "progress",
    "tests",
    "homework",
    "homework_done",
    "homework_note",
    "content",
    "summary",
    "상담내용",
    "기록",
    "특이사항",
    "확인사항",
    "요청사항",
)


LOG_CSV_SPECIAL_KEYWORDS = (
    *LOG_CSV_SPECIAL_LESSON_KEYWORDS,
    *LOG_CSV_TEXTBOOK_SPECIAL_KEYWORDS,
)


def log_csv_value_contains_keyword(value: str, keywords: tuple[str, ...]) -> bool:
    normalized_value = normalize_log_notes_text(value).lower()
    return any(keyword.lower() in normalized_value for keyword in keywords)


def log_csv_value_contains_special_keyword(value: str) -> bool:
    return log_csv_value_contains_keyword(value, LOG_CSV_SPECIAL_KEYWORDS)


def log_csv_value_without_textbook_special_keywords(value: str) -> str:
    normalized_value = normalize_log_notes_text(value).lower()
    for keyword in LOG_CSV_TEXTBOOK_SPECIAL_KEYWORDS:
        normalized_value = normalized_value.replace(keyword.lower(), " ")
    return normalized_value


def log_csv_value_contains_lesson_special_keyword(value: str) -> bool:
    normalized_without_textbook = log_csv_value_without_textbook_special_keywords(value)
    return any(keyword.lower() in normalized_without_textbook for keyword in LOG_CSV_SPECIAL_LESSON_KEYWORDS)


def normalize_log_csv_subject(value: str | None) -> str:
    normalized_value = log_csv_value_without_textbook_special_keywords(value)
    normalized_value = re.sub(r"\s+", "", normalized_value).lower()
    if not normalized_value:
        return ""
    if "공통수학1" in normalized_value or "공수1" in normalized_value:
        return "공수1"
    if "공통수학2" in normalized_value or "공수2" in normalized_value:
        return "공수2"
    if "미적분1" in normalized_value or "미적1" in normalized_value:
        return "미적1"
    if "미적분2" in normalized_value or "미적2" in normalized_value:
        return "미적2"
    if "확률과통계" in normalized_value or "확통" in normalized_value:
        return "확통"
    if "기하" in normalized_value:
        return "기하"
    if "대수" in normalized_value:
        return "대수"
    return ""


def find_log_csv_subject_from_columns(row: dict[str, str], columns: tuple[str, ...]) -> dict[str, str]:
    for column in columns:
        value = str(row.get(column) or "").strip()
        subject = normalize_log_csv_subject(value)
        if subject:
            return {
                "subject": subject,
                "column": column,
                "value": log_csv_special_value_preview(value),
            }
    return {"subject": "", "column": "", "value": ""}


def csv_subject_from_log_row(row: dict[str, str]) -> dict[str, str]:
    return find_log_csv_subject_from_columns(row, LOG_CSV_SUBJECT_COLUMNS)


def log_csv_date_weekday_label(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            parsed_date = datetime.strptime(text, fmt).date()
            return LOG_CSV_WEEKDAY_LABELS[parsed_date.weekday()]
        except ValueError:
            pass
    try:
        parsed_date = parse_log_export_date(text)
    except Exception:
        return ""
    return LOG_CSV_WEEKDAY_LABELS[parsed_date.weekday()]


def log_csv_date_key(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return parse_log_export_date(text).isoformat()
    except Exception:
        return ""


def log_csv_row_date_key(row: dict[str, str]) -> str:
    for column in LOG_CSV_DATE_COLUMNS:
        date_key = log_csv_date_key(row.get(column))
        if date_key:
            return date_key
    return ""


def log_csv_weekday_label_from_text(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    compact_text = re.sub(r"\s+", "", text).lower()
    for label in LOG_CSV_WEEKDAY_LABELS:
        if f"{label}요일" in compact_text or re.search(rf"(^|[^가-힣A-Za-z]){label}([12]?)(?=$|[^가-힣A-Za-z])", text):
            return label
        if re.search(rf"{label}[12]", compact_text):
            return label
    for token, label in LOG_CSV_ENGLISH_WEEKDAY_LABELS.items():
        if re.search(rf"(^|[^a-z]){re.escape(token)}(?=$|[^a-z])", compact_text):
            return label
    letters_only = re.sub(r"[^a-z]", "", compact_text)
    if letters_only in LOG_CSV_ENGLISH_WEEKDAY_LABELS:
        return LOG_CSV_ENGLISH_WEEKDAY_LABELS[letters_only]
    return ""


def log_csv_row_weekday_label(row: dict[str, str]) -> str:
    for column in LOG_CSV_DATE_COLUMNS:
        weekday_label = log_csv_date_weekday_label(row.get(column))
        if weekday_label:
            return weekday_label
    for column in LOG_CSV_WEEKDAY_COLUMNS:
        weekday_label = log_csv_weekday_label_from_text(row.get(column))
        if weekday_label:
            return weekday_label
    return ""


def normalize_log_csv_student_name(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def admin_student_candidate_text(candidate: dict[str, str]) -> str:
    parts: list[str] = []
    for key in ["detail", *ADMIN_STUDENT_MATCH_FIELDS]:
        value = candidate.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in parts:
            parts.append(text)
    return "\n".join(parts)


def admin_student_candidate_preview(candidate: dict[str, str]) -> str:
    preview_parts: list[str] = []
    for key in [
        "curriculum",
        "course",
        "subject",
        "class_type",
        "lesson_type",
        "course_type",
        "수업구분",
        "강의구분",
        "구분",
        "type",
        "category",
        "program",
        "subCurriculum",
        "day",
        "day1",
        "day2",
        "day3",
        "day4",
        "day5",
        "schedule",
        "time",
        "visitTime1",
        "visitTime2",
        "visitTime3",
        "visitTime4",
        "visitTime5",
        "start_time",
        "class",
        "class_name",
    ]:
        value = candidate.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in preview_parts:
            preview_parts.append(f"{key}={text}")
    if not preview_parts:
        detail = str(candidate.get("detail") or "").strip()
        if detail:
            preview_parts.append(detail)
    return log_csv_special_value_preview(" / ".join(preview_parts), max_length=180)


def admin_candidate_indexed_weekday_matches(candidate: dict[str, str], weekday_label: str) -> list[dict[str, str]]:
    if not weekday_label:
        return []
    matches: list[dict[str, str]] = []
    for day_field, time_field, label in ADMIN_STUDENT_INDEXED_SCHEDULE_PAIRS:
        day_value = str(candidate.get(day_field) or "").strip()
        if not day_value:
            continue
        parsed_weekday = log_csv_weekday_label_from_text(day_value)
        if parsed_weekday == weekday_label:
            matches.append(
                {
                    "label": label,
                    "day_field": day_field,
                    "time_field": time_field,
                    "day_value": day_value,
                    "time_value": str(candidate.get(time_field) or "").strip(),
                }
            )
    return matches


def admin_candidate_has_indexed_weekday_parse_failure(candidate: dict[str, str]) -> bool:
    for day_field, _time_field, _label in ADMIN_STUDENT_INDEXED_SCHEDULE_PAIRS:
        day_value = str(candidate.get(day_field) or "").strip()
        if day_value and not log_csv_weekday_label_from_text(day_value):
            return True
    return False


def admin_candidate_indexed_weekday_labels(candidate: dict[str, str]) -> set[str]:
    labels: set[str] = set()
    for day_field, _time_field, _label in ADMIN_STUDENT_INDEXED_SCHEDULE_PAIRS:
        weekday_label = log_csv_weekday_label_from_text(candidate.get(day_field))
        if weekday_label:
            labels.add(weekday_label)
    return labels


def admin_candidate_matches_weekday(candidate: dict[str, str], weekday_label: str) -> bool:
    if not weekday_label:
        return False
    if admin_candidate_indexed_weekday_matches(candidate, weekday_label):
        return True
    for key in ADMIN_STUDENT_SCHEDULE_FIELDS:
        if log_csv_weekday_label_from_text(candidate.get(key)) == weekday_label:
            return True
    text = admin_student_candidate_text(candidate)
    if not text.strip():
        return False
    compact_text = re.sub(r"\s+", "", text)
    if f"{weekday_label}요일" in compact_text:
        return True
    if re.search(rf"(^|[\n,;/|()\[\]<>_\-\s]){weekday_label}([12]?)(?=$|[\n,;/|()\[\]<>_\-\s])", text):
        return True
    if re.search(rf"{weekday_label}[12]", compact_text):
        return True
    for token, label in LOG_CSV_ENGLISH_WEEKDAY_LABELS.items():
        if label != weekday_label:
            continue
        if re.search(rf"(^|[^a-z]){re.escape(token)}(?=$|[^a-z])", compact_text.lower()):
            return True
    return False


def admin_candidate_actual_date_key(candidate: dict[str, str]) -> str:
    for column in ADMIN_STUDENT_ACTUAL_DATE_FIELDS:
        date_key = log_csv_date_key(candidate.get(column))
        if date_key:
            return date_key
    return ""


def admin_candidate_matches_actual_date(candidate: dict[str, str], row_date_key: str) -> bool:
    candidate_date_key = admin_candidate_actual_date_key(candidate)
    return bool(candidate_date_key and row_date_key and candidate_date_key == row_date_key)


def admin_subject_from_candidate(candidate: dict[str, str], subject_hint: str = "") -> dict[str, str]:
    clean_subject_hint = normalize_log_csv_subject(subject_hint)
    if clean_subject_hint:
        for column in ADMIN_STUDENT_SUBJECT_FIELDS:
            value = str(candidate.get(column) or "").strip()
            if normalize_log_csv_subject(value) == clean_subject_hint:
                return {
                    "subject": clean_subject_hint,
                    "column": column,
                    "value": log_csv_special_value_preview(value),
                }
    subject_info = find_log_csv_subject_from_columns(candidate, ADMIN_STUDENT_SUBJECT_FIELDS)
    if subject_info.get("subject"):
        return subject_info
    return {"subject": "", "column": "", "value": ""}


def admin_candidate_subject_match_field(candidate: dict[str, str], subject: str) -> str:
    clean_subject = normalize_log_csv_subject(subject)
    if not clean_subject:
        return ""
    for key in ADMIN_STUDENT_SUBJECT_FIELDS:
        if normalize_log_csv_subject(candidate.get(key)) == clean_subject:
            return key
    return ""


def admin_candidate_matches_subject(candidate: dict[str, str], subject: str) -> bool:
    return bool(admin_candidate_subject_match_field(candidate, subject))


def infer_lesson_type_from_explicit_fields(
    row: dict[str, str],
    columns: tuple[str, ...] = LOG_CSV_LESSON_TYPE_COLUMNS,
) -> dict[str, object]:
    regular_matches: list[dict[str, str]] = []
    special_matches: list[dict[str, str]] = []

    for column in columns:
        value = str(row.get(column) or "").strip()
        if not value:
            continue
        if log_csv_value_contains_keyword(value, LOG_CSV_REGULAR_LESSON_KEYWORDS):
            regular_matches.append({"컬럼": column, "값": log_csv_special_value_preview(value)})
        if log_csv_value_contains_lesson_special_keyword(value):
            special_matches.append({"컬럼": column, "값": log_csv_special_value_preview(value)})

    if regular_matches and special_matches:
        lesson_type = "확인 필요"
    elif special_matches:
        lesson_type = "특강"
    elif regular_matches:
        lesson_type = "정규강의"
    else:
        lesson_type = "미확인"

    matches = special_matches + regular_matches
    return {
        "lesson_type": lesson_type,
        "detected_columns": ", ".join(dict.fromkeys(match["컬럼"] for match in matches)) if matches else "-",
        "detected_values": " / ".join(f"{match['컬럼']}={match['값']}" for match in matches) if matches else "-",
        "has_conflict": bool(regular_matches and special_matches),
        "source": "명시 수업유형 필드",
    }


def admin_lesson_type_from_candidate(candidate: dict[str, str]) -> dict[str, object]:
    return infer_lesson_type_from_explicit_fields(candidate, ADMIN_STUDENT_LESSON_TYPE_FIELDS)


def known_lesson_types_from_admin_candidates(candidates: list[dict[str, str]]) -> set[str]:
    known_types: set[str] = set()
    for candidate in candidates:
        lesson_type = str(admin_lesson_type_from_candidate(candidate).get("lesson_type") or "")
        if lesson_type in {"정규강의", "특강"}:
            known_types.add(lesson_type)
    return known_types


def admin_candidate_matches_lesson_type(candidate: dict[str, str], lesson_type: str) -> bool:
    if lesson_type not in {"정규강의", "특강"}:
        return False
    candidate_lesson_type = str(admin_lesson_type_from_candidate(candidate).get("lesson_type") or "")
    return candidate_lesson_type == lesson_type


def admin_match_debug_reason(
    *,
    weekday_match: bool = False,
    subject_match: bool = False,
    type_match: bool = False,
    ambiguous_same_name: bool = False,
) -> str:
    reasons: list[str] = ["name_exact"]
    if weekday_match:
        reasons.append("weekday_match")
    if subject_match:
        reasons.append("subject_match")
    if type_match:
        reasons.append("type_match")
    if ambiguous_same_name:
        reasons.append("ambiguous_same_name")
    return ", ".join(reasons)


def log_csv_row_explicit_admin_sid(row: dict[str, str]) -> str:
    for key in ("admin_sid", "selected_admin_sid", "student_admin_sid"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def admin_candidate_admin_sid(candidate: dict[str, str]) -> str:
    for key in ("admin_sid", "sid", "id", "student_id", "studentId"):
        value = str(candidate.get(key) or "").strip()
        if value:
            return value
    return ""


def log_csv_row_time_hint(row: dict[str, str]) -> str:
    for key in ("time", "start_time", "startTime", "schedule", "class_time", "수업시간", "시간"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def normalize_admin_time_match_text(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def admin_candidate_time_match_details(
    candidate: dict[str, str],
    time_hint: str,
    weekday_label: str = "",
) -> list[str]:
    normalized_hint = re.sub(r"\s+", "", str(time_hint or "")).lower()
    if not normalized_hint:
        return []
    details: list[str] = []
    for match in admin_candidate_indexed_weekday_matches(candidate, weekday_label):
        time_field = match.get("time_field") or ""
        time_value = normalize_admin_time_match_text(candidate.get(time_field))
        if time_field and time_value and normalized_hint in time_value:
            details.append(time_field)
    if details:
        return details

    non_indexed_time_fields = ("time", "start_time", "startTime", "schedule", "class_time", "수업시간", "시간")
    for key in non_indexed_time_fields:
        normalized_value = normalize_admin_time_match_text(candidate.get(key))
        if normalized_value and normalized_hint in normalized_value:
            details.append(key)
    if details:
        return details

    if not weekday_label or not admin_candidate_indexed_weekday_labels(candidate):
        for _day_field, time_field, _label in ADMIN_STUDENT_INDEXED_SCHEDULE_PAIRS:
            normalized_value = normalize_admin_time_match_text(candidate.get(time_field))
            if normalized_value and normalized_hint in normalized_value:
                details.append(time_field)
    return details


def admin_candidate_matches_time_hint(candidate: dict[str, str], time_hint: str, weekday_label: str = "") -> bool:
    if admin_candidate_time_match_details(candidate, time_hint, weekday_label):
        return True
    normalized_hint = re.sub(r"\s+", "", str(time_hint or "")).lower()
    if not normalized_hint:
        return False
    candidate_text = "\n".join(
        str(candidate.get(key) or "").strip()
        for key in ADMIN_STUDENT_SCHEDULE_FIELDS
        if str(candidate.get(key) or "").strip()
    )
    normalized_candidate = re.sub(r"\s+", "", candidate_text).lower()
    return bool(normalized_candidate and normalized_hint in normalized_candidate)


def score_admin_candidate_for_log_row(
    candidate: dict[str, str],
    row: dict[str, str],
    weekday_label: str,
    clean_subject_hint: str,
    row_lesson_type: str,
    row_date_key: str = "",
) -> dict[str, object]:
    score = 0
    reasons: list[str] = []
    name_exact = normalize_log_csv_student_name(candidate.get("student_name")) == normalize_log_csv_student_name(row.get("name"))
    if name_exact:
        score += 10
        reasons.append("name_exact")

    actual_date_match = admin_candidate_matches_actual_date(candidate, row_date_key)
    candidate_date_key = admin_candidate_actual_date_key(candidate)
    if actual_date_match:
        score += 50
        reasons.append("actual_date_match")
    elif row_date_key and candidate_date_key:
        score -= 5
        reasons.append("actual_date_mismatch")

    indexed_weekday_matches = admin_candidate_indexed_weekday_matches(candidate, weekday_label)
    weekday_parse_failed = admin_candidate_has_indexed_weekday_parse_failure(candidate)
    weekday_match = bool(indexed_weekday_matches) or admin_candidate_matches_weekday(candidate, weekday_label)
    if weekday_match:
        score += 10
        reasons.append("weekday_match")
        if indexed_weekday_matches:
            reasons.append(
                "day_index_match:"
                + ",".join(match.get("label", "") for match in indexed_weekday_matches if match.get("label"))
            )
    elif weekday_label:
        score -= 3
        reasons.append("weekday_mismatch")
        if weekday_parse_failed:
            reasons.append("weekday_parse_failed")

    curriculum_subject = normalize_log_csv_subject(candidate.get("curriculum"))
    sub_curriculum_subject = normalize_log_csv_subject(candidate.get("subCurriculum"))
    candidate_subject_values = [
        normalize_log_csv_subject(candidate.get(key))
        for key in ADMIN_STUDENT_SUBJECT_FIELDS
        if normalize_log_csv_subject(candidate.get(key))
    ]
    subject_match_field = admin_candidate_subject_match_field(candidate, clean_subject_hint)
    subject_match = bool(subject_match_field)
    curriculum_match = bool(clean_subject_hint and curriculum_subject == clean_subject_hint)
    sub_curriculum_match = bool(clean_subject_hint and sub_curriculum_subject == clean_subject_hint)
    curriculum_conflict = bool(clean_subject_hint and curriculum_subject and curriculum_subject != clean_subject_hint)
    subject_conflict = bool(clean_subject_hint and candidate_subject_values and not subject_match)
    if subject_match:
        score += 16 if subject_match_field == "subCurriculum" else 12
        reasons.append("subject_match")
        reasons.append("csv_subject_match")
        if subject_match_field == "curriculum":
            reasons.append("curriculum_match")
        elif subject_match_field == "subCurriculum":
            reasons.append("sub_curriculum_match")
        if curriculum_conflict:
            reasons.append("subject_conflict:curriculum")
    elif subject_conflict:
        score -= 6
        reasons.append("subject_conflict")

    type_match = admin_candidate_matches_lesson_type(candidate, row_lesson_type)
    explicit_type_missing = not any(str(candidate.get(key) or "").strip() for key in ADMIN_STUDENT_LESSON_TYPE_FIELDS)
    if type_match:
        score += 6
        reasons.append("type_match")
    elif explicit_type_missing:
        reasons.append("explicit_type_missing")

    time_hint = log_csv_row_time_hint(row)
    time_match_details = admin_candidate_time_match_details(candidate, time_hint, weekday_label)
    time_match = bool(time_match_details)
    if time_match:
        score += 4
        reasons.append("time_match")
        if any(str(detail).startswith("visitTime") for detail in time_match_details):
            reasons.append("visit_time_match:" + ",".join(time_match_details))

    explicit_admin_sid = log_csv_row_explicit_admin_sid(row)
    candidate_admin_sid = admin_candidate_admin_sid(candidate)
    explicit_sid_match = bool(explicit_admin_sid and candidate_admin_sid == explicit_admin_sid)
    if explicit_sid_match:
        score += 100
        reasons.append("explicit_admin_sid")

    return {
        "candidate": candidate,
        "score": score,
        "reasons": reasons,
        "name_exact": name_exact,
        "weekday_match": weekday_match,
        "day_index_match": ",".join(match.get("label", "") for match in indexed_weekday_matches if match.get("label")),
        "weekday_parse_failed": weekday_parse_failed,
        "actual_date_match": actual_date_match,
        "subject_match": subject_match,
        "subject_match_field": subject_match_field,
        "csv_subject": clean_subject_hint,
        "csv_subject_match": subject_match,
        "curriculum_match": curriculum_match,
        "sub_curriculum_match": sub_curriculum_match,
        "subject_conflict": bool(subject_conflict or curriculum_conflict),
        "selected_by_csv_subject": bool(clean_subject_hint and subject_match),
        "type_match": type_match,
        "explicit_type_missing": explicit_type_missing,
        "time_match": time_match,
        "visit_time_match": ",".join(time_match_details),
        "explicit_sid_match": explicit_sid_match,
    }


def select_scored_admin_candidate(scored_candidates: list[dict[str, object]]) -> tuple[dict[str, object] | None, str]:
    if not scored_candidates:
        return None, "후보 데이터가 비어 있습니다."
    sorted_candidates = sorted(scored_candidates, key=lambda item: int(item.get("score") or 0), reverse=True)
    top = sorted_candidates[0]
    if not bool(top.get("name_exact")):
        return None, "최고점 후보도 학생명이 정확히 일치하지 않아 자동 선택하지 않습니다."
    if len(sorted_candidates) == 1:
        return top, "후보 1개"

    top_score = int(top.get("score") or 0)
    second_score = int(sorted_candidates[1].get("score") or 0)
    top_ties = [item for item in sorted_candidates if int(item.get("score") or 0) == top_score]
    if len(top_ties) > 1:
        return None, f"최고점 {top_score}점 후보가 {len(top_ties)}개입니다."

    margin = top_score - second_score
    strong_core_match = bool(top.get("name_exact") and top.get("weekday_match") and top.get("subject_match"))
    explicit_sid_match = bool(top.get("explicit_sid_match"))
    if explicit_sid_match or margin >= 2 or strong_core_match:
        return top, f"1등/2등 점수 차이 {margin}점"
    return None, f"1등/2등 점수 차이 {margin}점으로 자동 선택 기준에 부족합니다."


def format_admin_candidate_scores(scored_candidates: list[dict[str, object]]) -> str:
    formatted: list[str] = []
    for item in sorted(scored_candidates, key=lambda value: int(value.get("score") or 0), reverse=True):
        candidate = item.get("candidate") or {}
        if not isinstance(candidate, dict):
            continue
        label = admin_student_candidate_preview(candidate)
        reasons = ", ".join(str(reason) for reason in item.get("reasons", []) if str(reason).strip())
        formatted.append(f"{int(item.get('score') or 0)}점 [{reasons or '-'}] {label}")
    return " / ".join(formatted[:5])


def build_admin_subject_match_success_result(
    *,
    selected_candidate: dict[str, str],
    row: dict[str, str],
    weekday_label: str,
    clean_subject_hint: str,
    same_name_count: int,
    weekday_count: int,
    subject_weekday_count: int,
    scored_candidates: list[dict[str, object]],
    select_reason: str,
    actual_date_count: int = 0,
) -> dict[str, object]:
    subject_info = admin_subject_from_candidate(selected_candidate, clean_subject_hint)
    selected_lesson_type_info = admin_lesson_type_from_candidate(selected_candidate)
    subject = str(subject_info.get("subject") or clean_subject_hint or "")
    top_score = max((int(item.get("score") or 0) for item in scored_candidates), default=0)
    sorted_scores = sorted((int(item.get("score") or 0) for item in scored_candidates), reverse=True)
    score_margin = top_score - sorted_scores[1] if len(sorted_scores) > 1 else top_score
    selected_score_item = next(
        (item for item in scored_candidates if item.get("candidate") is selected_candidate),
        {},
    )
    admin_subject_source_column = str(subject_info.get("column") or "")
    if admin_subject_source_column == "subCurriculum":
        selected_subject_source = "admin_sub_curriculum"
    elif admin_subject_source_column == "curriculum":
        selected_subject_source = "admin_curriculum"
    elif admin_subject_source_column:
        selected_subject_source = "admin_subject_field"
    else:
        selected_subject_source = "fallback"
    match_reason = ", ".join(
        str(reason) for reason in selected_score_item.get("reasons", []) if str(reason).strip()
    ) or admin_match_debug_reason(weekday_match=True)
    return {
        "subject": subject,
        "csv_subject": clean_subject_hint,
        "final_subject": subject,
        "csv_subject_match": bool(selected_score_item.get("csv_subject_match")),
        "curriculum_match": bool(selected_score_item.get("curriculum_match")),
        "sub_curriculum_match": bool(selected_score_item.get("sub_curriculum_match")),
        "selected_subject_source": selected_subject_source,
        "subject_conflict": bool(selected_score_item.get("subject_conflict")),
        "selected_by_csv_subject": bool(selected_score_item.get("selected_by_csv_subject")),
        "admin_subject_source_column": admin_subject_source_column,
        "weekday": weekday_label,
        "status": "매칭 성공",
        "candidate_count": same_name_count,
        "weekday_candidate_count": weekday_count,
        "actual_date_candidate_count": actual_date_count,
        "subject_weekday_candidate_count": subject_weekday_count,
        "candidate_preview": admin_student_candidate_preview(selected_candidate),
        "reason": f"{select_reason} 기준으로 자동 선택했습니다.",
        "lesson_type_info": selected_lesson_type_info,
        "matched_class_type": selected_lesson_type_info.get("lesson_type") or "",
        "match_confidence": "auto",
        "match_reason": match_reason,
        "match_needs_review": False,
        "candidate_scores": format_admin_candidate_scores(scored_candidates),
        "top_score": top_score,
        "second_score_margin": score_margin,
    }


def infer_admin_subject_for_log_row(
    row: dict[str, str],
    admin_rows: list[dict[str, str]],
    admin_error: str = "",
    subject_hint: str = "",
) -> dict[str, object]:
    weekday_label = log_csv_row_weekday_label(row)
    row_date_key = log_csv_row_date_key(row)
    name = str(row.get("name") or "").strip()
    name_key = normalize_log_csv_student_name(name)
    clean_subject_hint = normalize_log_csv_subject(subject_hint)
    if admin_error:
        return {
            "subject": "",
            "weekday": weekday_label,
            "status": "admin 연결 실패",
            "candidate_count": 0,
            "weekday_candidate_count": 0,
            "candidate_preview": "",
            "reason": admin_error,
        }
    if not name:
        return {
            "subject": "",
            "weekday": weekday_label,
            "status": "학생명 없음",
            "candidate_count": 0,
            "weekday_candidate_count": 0,
            "candidate_preview": "",
            "reason": "CSV row에 학생명이 없습니다.",
        }
    same_name_candidates = [
        candidate
        for candidate in admin_rows
        if normalize_log_csv_student_name(candidate.get("student_name")) == name_key
    ]
    weekday_candidates = [
        candidate
        for candidate in same_name_candidates
        if admin_candidate_matches_weekday(candidate, weekday_label)
    ]
    actual_date_candidates = [
        candidate
        for candidate in same_name_candidates
        if admin_candidate_matches_actual_date(candidate, row_date_key)
    ]
    subject_weekday_candidates = [
        candidate
        for candidate in weekday_candidates
        if admin_candidate_matches_subject(candidate, clean_subject_hint)
    ]
    time_hint = log_csv_row_time_hint(row)
    time_weekday_candidates = [
        candidate
        for candidate in weekday_candidates
        if admin_candidate_time_match_details(candidate, time_hint, weekday_label)
    ]
    row_lesson_type_info = infer_lesson_type_from_log_row(row)
    row_lesson_type = str(row_lesson_type_info.get("lesson_type") or "")
    lesson_type_weekday_candidates = [
        candidate
        for candidate in weekday_candidates
        if admin_candidate_matches_lesson_type(candidate, row_lesson_type)
    ]
    subject_lesson_type_weekday_candidates = [
        candidate
        for candidate in subject_weekday_candidates
        if admin_candidate_matches_lesson_type(candidate, row_lesson_type)
    ]
    known_weekday_lesson_types = known_lesson_types_from_admin_candidates(weekday_candidates)
    scored_candidates: list[dict[str, object]] = []
    if not same_name_candidates:
        status = "admin 후보 없음"
        reason = "admin 학생 목록에서 같은 학생명을 찾지 못했습니다."
    else:
        if len(actual_date_candidates) == 1:
            selected_candidate = actual_date_candidates[0]
            scored_candidates = [
                score_admin_candidate_for_log_row(
                    selected_candidate,
                    row,
                    weekday_label,
                    clean_subject_hint,
                    row_lesson_type,
                    row_date_key,
                )
            ]
            return build_admin_subject_match_success_result(
                selected_candidate=selected_candidate,
                row=row,
                weekday_label=weekday_label,
                clean_subject_hint=clean_subject_hint,
                same_name_count=len(same_name_candidates),
                weekday_count=len(weekday_candidates),
                subject_weekday_count=len(subject_weekday_candidates),
                scored_candidates=scored_candidates,
                actual_date_count=len(actual_date_candidates),
                select_reason=f"ADMIN 실제 수업일 {row_date_key} 일치 후보 1개",
            )

        if not actual_date_candidates and len(time_weekday_candidates) == 1:
            selected_candidate = time_weekday_candidates[0]
            scored_candidates = [
                score_admin_candidate_for_log_row(
                    selected_candidate,
                    row,
                    weekday_label,
                    clean_subject_hint,
                    row_lesson_type,
                    row_date_key,
                )
            ]
            return build_admin_subject_match_success_result(
                selected_candidate=selected_candidate,
                row=row,
                weekday_label=weekday_label,
                clean_subject_hint=clean_subject_hint,
                same_name_count=len(same_name_candidates),
                weekday_count=len(weekday_candidates),
                subject_weekday_count=len(subject_weekday_candidates),
                scored_candidates=scored_candidates,
                actual_date_count=len(actual_date_candidates),
                select_reason=f"CSV 날짜 기준 {weekday_label}요일과 시간 {time_hint} 일치 후보 1개",
            )

        if not actual_date_candidates and len(weekday_candidates) == 1:
            selected_candidate = weekday_candidates[0]
            scored_candidates = [
                score_admin_candidate_for_log_row(
                    selected_candidate,
                    row,
                    weekday_label,
                    clean_subject_hint,
                    row_lesson_type,
                    row_date_key,
                )
            ]
            return build_admin_subject_match_success_result(
                selected_candidate=selected_candidate,
                row=row,
                weekday_label=weekday_label,
                clean_subject_hint=clean_subject_hint,
                same_name_count=len(same_name_candidates),
                weekday_count=len(weekday_candidates),
                subject_weekday_count=len(subject_weekday_candidates),
                scored_candidates=scored_candidates,
                actual_date_count=len(actual_date_candidates),
                select_reason=f"CSV 날짜 기준 {weekday_label}요일 일치 후보 1개",
            )

        score_pool = actual_date_candidates or time_weekday_candidates or weekday_candidates or same_name_candidates
        scored_candidates = [
            score_admin_candidate_for_log_row(
                candidate,
                row,
                weekday_label,
                clean_subject_hint,
                row_lesson_type,
                row_date_key,
            )
            for candidate in score_pool
        ]
        selected_score, select_reason = select_scored_admin_candidate(scored_candidates)
        if selected_score:
            selected_candidate = selected_score.get("candidate") or {}
            if isinstance(selected_candidate, dict):
                if actual_date_candidates:
                    select_reason = f"ADMIN 실제 수업일 {row_date_key} 후보 점수화 · {select_reason}"
                elif not weekday_candidates and weekday_label:
                    select_reason = f"요일 일치 후보 없음 · {select_reason}"
                elif not weekday_label:
                    select_reason = f"날짜 요일 파싱 불가 · {select_reason}"
                return build_admin_subject_match_success_result(
                    selected_candidate=selected_candidate,
                    row=row,
                    weekday_label=weekday_label,
                    clean_subject_hint=clean_subject_hint,
                    same_name_count=len(same_name_candidates),
                    weekday_count=len(weekday_candidates),
                    subject_weekday_count=len(subject_weekday_candidates),
                    scored_candidates=scored_candidates,
                    actual_date_count=len(actual_date_candidates),
                    select_reason=select_reason,
                )
        status = "후보 여러 개"
        if not weekday_label:
            status = "날짜 없음"
        elif actual_date_candidates:
            status = "같은 날짜 후보 여러 개"
        elif not weekday_candidates:
            status = "요일 후보 없음"
        elif len(weekday_candidates) == 1:
            status = "과목 미확인"
        reason = select_reason

    preview_candidates = weekday_candidates or same_name_candidates
    single_weekday_lesson_type_info = (
        admin_lesson_type_from_candidate(weekday_candidates[0])
        if len(weekday_candidates) == 1
        else {}
    )
    return {
        "subject": "",
        "weekday": weekday_label,
        "status": status,
        "candidate_count": len(same_name_candidates),
        "weekday_candidate_count": len(weekday_candidates),
        "actual_date_candidate_count": len(actual_date_candidates),
        "subject_weekday_candidate_count": len(subject_weekday_candidates),
        "candidate_preview": " / ".join(
            admin_student_candidate_preview(candidate) for candidate in preview_candidates[:3]
        ),
        "reason": reason,
        "candidate_lesson_types": ", ".join(sorted(known_weekday_lesson_types)) if known_weekday_lesson_types else "",
        "candidate_scores": format_admin_candidate_scores(scored_candidates),
        "lesson_type_info": single_weekday_lesson_type_info,
        "matched_class_type": single_weekday_lesson_type_info.get("lesson_type") or "",
        "match_confidence": "review" if status in {"후보 여러 개", "같은 날짜 후보 여러 개", "과목 불일치", "날짜 없음", "요일 후보 없음"} else "",
        "match_reason": admin_match_debug_reason(
            weekday_match=bool(weekday_candidates),
            subject_match=bool(subject_weekday_candidates),
            ambiguous_same_name=status in {"후보 여러 개", "같은 날짜 후보 여러 개"},
        ),
        "match_needs_review": status in {"후보 여러 개", "같은 날짜 후보 여러 개", "과목 불일치", "날짜 없음", "요일 후보 없음"},
    }


def build_applied_subject_info(
    row: dict[str, str],
    admin_subject_info: dict[str, object],
) -> dict[str, object]:
    csv_subject_info = csv_subject_from_log_row(row)
    csv_subject = str(csv_subject_info.get("subject") or "")
    admin_subject = str(admin_subject_info.get("subject") or "")
    actual_date_candidate_count = int(admin_subject_info.get("actual_date_candidate_count") or 0)
    admin_actual_date_matched = (
        bool(admin_subject)
        and str(admin_subject_info.get("status") or "") == "매칭 성공"
        and actual_date_candidate_count > 0
    )

    admin_subject_source = str(admin_subject_info.get("selected_subject_source") or "")
    selected_by_csv_subject = bool(admin_subject_info.get("selected_by_csv_subject"))
    subject_conflict = bool(admin_subject_info.get("subject_conflict"))

    if csv_subject:
        applied_subject = csv_subject
        applied_source = "CSV 명시 과목"
        selected_subject_source = "csv_explicit"
        selected_by_csv_subject = True
    elif admin_actual_date_matched:
        applied_subject = admin_subject
        applied_source = "admin 실제 수업일 매칭"
        selected_subject_source = admin_subject_source or "admin_actual_date"
    elif admin_subject:
        applied_subject = admin_subject
        if admin_subject_source == "admin_sub_curriculum":
            applied_source = "admin subCurriculum 매칭"
            selected_subject_source = "admin_sub_curriculum"
        elif admin_subject_source == "admin_curriculum":
            applied_source = "admin curriculum 매칭"
            selected_subject_source = "admin_curriculum"
        else:
            applied_source = "admin 시간표 매칭"
            selected_subject_source = admin_subject_source or "admin_subject"
    else:
        applied_subject = ""
        applied_source = "미확인"
        selected_subject_source = "fallback"

    final_subject = applied_subject

    return {
        "csv_subject": csv_subject,
        "csv_subject_source": csv_subject_info.get("column") or "",
        "csv_subject_value": csv_subject_info.get("value") or "",
        "admin_subject": admin_subject,
        "applied_subject": applied_subject,
        "final_subject": final_subject,
        "applied_source": applied_source,
        "selected_subject_source": selected_subject_source,
        "selected_by_csv_subject": selected_by_csv_subject,
        "csv_subject_match": bool(admin_subject_info.get("csv_subject_match") or (csv_subject and csv_subject == admin_subject)),
        "curriculum_match": bool(admin_subject_info.get("curriculum_match")),
        "sub_curriculum_match": bool(admin_subject_info.get("sub_curriculum_match")),
        "subject_conflict": subject_conflict,
        "admin_subject_source_column": admin_subject_info.get("admin_subject_source_column") or "",
        "admin_status": admin_subject_info.get("status") or "미확인",
        "admin_weekday": admin_subject_info.get("weekday") or "",
        "admin_candidate_count": admin_subject_info.get("candidate_count") or 0,
        "admin_weekday_candidate_count": admin_subject_info.get("weekday_candidate_count") or 0,
        "admin_actual_date_candidate_count": actual_date_candidate_count,
        "admin_candidate_preview": admin_subject_info.get("candidate_preview") or "",
        "admin_reason": admin_subject_info.get("reason") or "",
        "admin_subject_weekday_candidate_count": admin_subject_info.get("subject_weekday_candidate_count") or 0,
        "matched_class_type": admin_subject_info.get("matched_class_type") or "",
        "match_confidence": admin_subject_info.get("match_confidence") or "",
        "match_reason": admin_subject_info.get("match_reason") or "",
        "match_needs_review": admin_subject_info.get("match_needs_review") or False,
        "candidate_lesson_types": admin_subject_info.get("candidate_lesson_types") or "",
        "candidate_scores": admin_subject_info.get("candidate_scores") or "",
        "top_score": admin_subject_info.get("top_score") or "",
        "second_score_margin": admin_subject_info.get("second_score_margin") or "",
    }


def infer_lesson_type_from_log_row(row: dict[str, str]) -> dict[str, object]:
    return infer_lesson_type_from_explicit_fields(row, LOG_CSV_LESSON_TYPE_COLUMNS)


def infer_lesson_type_for_log_item(
    row: dict[str, str],
    admin_subject_info: dict[str, object],
) -> dict[str, object]:
    admin_lesson_type_info = admin_subject_info.get("lesson_type_info")
    if isinstance(admin_lesson_type_info, dict):
        admin_lesson_type = str(admin_lesson_type_info.get("lesson_type") or "")
        if admin_lesson_type in {"정규강의", "특강"}:
            return {
                **admin_lesson_type_info,
                "source": "admin 명시 수업유형 필드",
            }

    csv_lesson_type_info = infer_lesson_type_from_log_row(row)
    csv_lesson_type = str(csv_lesson_type_info.get("lesson_type") or "")
    if csv_lesson_type in {"정규강의", "특강", "확인 필요"}:
        return {
            **csv_lesson_type_info,
            "source": "CSV 명시 수업유형 필드",
        }

    if admin_subject_info.get("match_needs_review") or admin_subject_info.get("status") == "후보 여러 개":
        return {
            "lesson_type": "확인 필요",
            "detected_columns": "-",
            "detected_values": str(admin_subject_info.get("candidate_lesson_types") or "-"),
            "has_conflict": True,
            "source": "admin 후보 여러 개",
        }

    return {
        **csv_lesson_type_info,
        "source": "명시 수업유형 필드 없음",
    }


def log_csv_special_value_preview(value: str, max_length: int = 80) -> str:
    preview = re.sub(r"\s+", " ", normalize_log_notes_text(value)).strip()
    if len(preview) <= max_length:
        return preview
    return preview[: max_length - 1] + "…"


def log_csv_special_text_matches(row: dict[str, str]) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    for column in LOG_CSV_SPECIAL_CLASS_COLUMNS:
        value = str(row.get(column) or "").strip()
        if value and log_csv_value_contains_lesson_special_keyword(value):
            matches.append(
                {
                    "위치": "수업 구분 컬럼",
                    "컬럼": column,
                    "값": log_csv_special_value_preview(value),
                }
            )
    for column in LOG_CSV_SPECIAL_CONTENT_COLUMNS:
        value = str(row.get(column) or "").strip()
        if value and log_csv_value_contains_keyword(value, LOG_CSV_TEXTBOOK_SPECIAL_KEYWORDS):
            matches.append(
                {
                    "위치": "수능특강 내용 컬럼",
                    "컬럼": column,
                    "값": log_csv_special_value_preview(value),
                }
            )
    return matches


def log_csv_detected_content_columns(row: dict[str, str]) -> list[str]:
    return [
        column
        for column in LOG_EXPORT_CONTENT_COLUMNS
        if normalize_log_notes_text(row.get(column))
    ]


def log_csv_special_class_exclusion_reason(
    item: dict[str, object],
    missing_columns: list[str],
) -> str:
    row = item["source_row"]
    docs_matches = item.get("docs_matches") or []
    reasons: list[str] = []

    if missing_columns:
        reasons.append(f"필수 컬럼 없음: {', '.join(missing_columns)}")
    if not str(row.get("date") or "").strip():
        reasons.append("날짜 없음")
    if not str(row.get("name") or "").strip():
        reasons.append("student_name 불일치")
    if not log_row_has_docs_content(row):
        reasons.append("기록 내용 없음")
    if len(docs_matches) == 0:
        reasons.append("Google Docs 문서 매핑 없음 또는 student_name 불일치")
    elif len(docs_matches) > 1:
        reasons.append("Google Docs 문서 매핑 중복")
    if not bool(item.get("selected")) and not reasons:
        reasons.append("현재 필터/선택 조건에서 제외됨")

    return " / ".join(reasons) if reasons else "선택 가능"


def build_special_class_diagnostic_rows(
    matched_rows: list[dict[str, object]],
    missing_columns: list[str],
) -> list[dict[str, object]]:
    diagnostic_rows: list[dict[str, object]] = []
    for item in matched_rows:
        row = item["source_row"]
        special_matches = log_csv_special_text_matches(row)
        if not special_matches:
            continue

        docs_matches = item.get("docs_matches") or []
        content_columns = log_csv_detected_content_columns(row)
        has_doc_target = (
            len(docs_matches) == 1
            and bool((docs_matches[0].get("doc_id") or docs_matches[0].get("doc_url") or "").strip())
        )
        if len(docs_matches) == 1:
            docs_match_status = "성공"
        elif len(docs_matches) > 1:
            docs_match_status = "중복"
        else:
            docs_match_status = "없음"

        diagnostic_rows.append(
            {
                "원본 행": item.get("row_index", ""),
                "name": str(row.get("name") or "").strip(),
                "관련 감지 위치": ", ".join(sorted({match["위치"] for match in special_matches})),
                "관련 감지 컬럼": ", ".join(match["컬럼"] for match in special_matches),
                "관련 감지 값 일부": " / ".join(
                    f"{match['컬럼']}={match['값']}" for match in special_matches
                ),
                "Docs 학생명 매칭": docs_match_status,
                "doc_url/doc_id 존재": "예" if has_doc_target else "아니오",
                "기록 내용 인식": "예" if content_columns else "아니오",
                "내용 감지 컬럼": ", ".join(content_columns) if content_columns else "-",
                "선택 가능": "예" if item.get("selected") else "아니오",
                "제외/진단 사유": log_csv_special_class_exclusion_reason(item, missing_columns),
            }
        )
    return diagnostic_rows


def build_lesson_type_diagnostic_rows(matched_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    diagnostic_rows: list[dict[str, object]] = []
    for item in matched_rows:
        row = item["source_row"]
        lesson_type_info = item.get("lesson_type_info") or infer_lesson_type_from_log_row(row)
        applied_subject_info = item.get("applied_subject_info") or {}
        diagnostic_rows.append(
            {
                "원본 행": item.get("row_index", ""),
                "name": str(row.get("name") or "").strip(),
                "date": str(row.get("date") or "").strip(),
                "요일": str(applied_subject_info.get("admin_weekday") or ""),
                "curriculum": str(row.get("curriculum") or "").strip(),
                "CSV 과목": str(applied_subject_info.get("csv_subject") or "-"),
                "admin 기준 과목": str(applied_subject_info.get("admin_subject") or "-"),
                "최종 적용 과목": str(applied_subject_info.get("applied_subject") or "미확인"),
                "적용 방식": str(applied_subject_info.get("applied_source") or "미확인"),
                "선택 과목 출처": str(applied_subject_info.get("selected_subject_source") or "-"),
                "CSV 과목 매칭": "예" if applied_subject_info.get("csv_subject_match") else "아니오",
                "curriculum 매칭": "예" if applied_subject_info.get("curriculum_match") else "아니오",
                "subCurriculum 매칭": "예" if applied_subject_info.get("sub_curriculum_match") else "아니오",
                "과목 충돌": "예" if applied_subject_info.get("subject_conflict") else "아니오",
                "CSV 과목 우선": "예" if applied_subject_info.get("selected_by_csv_subject") else "아니오",
                "admin 매칭 상태": str(applied_subject_info.get("admin_status") or "미확인"),
                "admin 후보 수": applied_subject_info.get("admin_candidate_count", 0),
                "같은 날짜 admin 후보 수": applied_subject_info.get("admin_actual_date_candidate_count", 0),
                "요일 후보 수": applied_subject_info.get("admin_weekday_candidate_count", 0),
                "요일+과목 후보 수": applied_subject_info.get("admin_subject_weekday_candidate_count", 0),
                "admin 후보 일부": str(applied_subject_info.get("admin_candidate_preview") or "-"),
                "확인 필요 사유": str(applied_subject_info.get("admin_reason") or "-"),
                "수업 유형": str(lesson_type_info.get("lesson_type") or "미확인"),
                "수업 유형 근거": str(lesson_type_info.get("source") or "-"),
                "수업 유형 감지 컬럼": str(lesson_type_info.get("detected_columns") or "-"),
                "수업 유형 감지 값": str(lesson_type_info.get("detected_values") or "-"),
                "정규/특강 동시 감지": "예" if lesson_type_info.get("has_conflict") else "아니오",
                "admin 명시 수업유형": str(applied_subject_info.get("matched_class_type") or "-"),
                "매칭 신뢰도": str(applied_subject_info.get("match_confidence") or "-"),
                "매칭 근거": str(applied_subject_info.get("match_reason") or "-"),
                "매칭 확인 필요": "예" if applied_subject_info.get("match_needs_review") else "아니오",
                "admin 후보 수업유형": str(applied_subject_info.get("candidate_lesson_types") or "-"),
                "후보별 점수": str(applied_subject_info.get("candidate_scores") or "-"),
                "1등 점수": str(applied_subject_info.get("top_score") or "-"),
                "1등/2등 점수 차이": str(applied_subject_info.get("second_score_margin") or "-"),
                "수능특강/수특 유형 제외": "예",
                "선택 가능": "예" if item.get("selected") else "아니오",
                "상태": str(item.get("status") or ""),
            }
        )
    return diagnostic_rows


def normalize_log_csv_duplicate_text(value: str | None) -> str:
    normalized = normalize_log_notes_text(value)
    normalized = normalized.strip().strip("\"'“”‘’")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().lower()


def normalize_counsel_duplicate_fingerprint_text(value: object) -> str:
    text = str(value or "").lower()
    text = text.replace("：", ":").replace("（", "(").replace("）", ")")
    text = re.sub(r"다음\s*숙제", "다음숙제", text)
    text = re.sub(r"이번\s*숙제", "이번숙제", text)
    text = re.sub(r"진행\s*률", "진행률", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[,.\-_\[\]():/|]+", "", text)
    return text.strip()


def strip_counsel_duplicate_bullet(line: str) -> str:
    return re.sub(r"^[\-•·]\s*", "", str(line or "").strip()).strip()


def is_counsel_homework_table_header(line: str) -> bool:
    normalized = normalize_counsel_duplicate_fingerprint_text(line)
    return "교재" in normalized and "진행률" in normalized and "다음숙제" in normalized


def parse_counsel_homework_fingerprint_line(line: str) -> dict[str, str] | None:
    clean_line = strip_counsel_duplicate_bullet(line)
    if not clean_line or is_counsel_homework_table_header(clean_line):
        return None

    if "/" in clean_line:
        parts = [part.strip() for part in clean_line.split("/")]
        book = parts[0] if parts else ""
        if not book or normalize_counsel_duplicate_fingerprint_text(book) == "교재":
            return None
        progress = parts[2] if len(parts) >= 5 else (parts[2] if len(parts) > 2 and "%" in parts[2] else "")
        next_homework = parts[4] if len(parts) >= 5 else (parts[3] if len(parts) > 3 else "")
        return {"book": book, "progress": progress, "next": next_homework, "text": ""}

    match = re.match(r"^([^:：]+)[:：]\s*(.+)$", clean_line)
    if not match:
        return None
    book = match.group(1).strip()
    rest = match.group(2).strip()
    if not book or re.match(r"^(진도|특이사항|상담\s*참고사항|오늘\s*확인할\s*것)$", book):
        return None
    progress_match = re.search(r"진행률\s*([0-9]+%)", rest)
    next_match = re.search(r"다음\s*숙제\s*([^,，]+)", rest)
    fallback_text = re.sub(r"진행률\s*[0-9]+%", "", rest)
    fallback_text = re.sub(r"다음\s*숙제\s*", "", fallback_text)
    fallback_text = re.sub(r"[,，]", " ", fallback_text).strip()
    return {
        "book": book,
        "progress": progress_match.group(1) if progress_match else "",
        "next": next_match.group(1).strip() if next_match else fallback_text,
        "text": "",
    }


def extract_counsel_duplicate_note_text(line: str) -> str:
    clean_line = strip_counsel_duplicate_bullet(line)
    if not clean_line or re.match(r"^\[.*\]$", clean_line):
        return ""
    if re.match(r"^(상담\s*참고사항|오늘\s*확인할\s*것|수업\s*전\s*확인|숙제\s*체크)$", clean_line):
        return ""
    note_match = re.match(r"^(특이사항|상담\s*참고사항)\s*[:：]\s*(.+)$", clean_line)
    if note_match:
        return normalize_counsel_duplicate_fingerprint_text(note_match.group(2))
    return normalize_counsel_duplicate_fingerprint_text(clean_line)


def build_log_csv_duplicate_summary_fingerprint(summary: object) -> str:
    lines = [
        line.strip()
        for line in str(summary or "").replace("\r", "\n").split("\n")
        if line.strip()
    ]
    progress = ""
    notes: list[str] = []
    homework: dict[str, dict[str, str]] = {}
    in_homework = False

    for raw_line in lines:
        line = strip_counsel_duplicate_bullet(raw_line)
        progress_match = re.match(r"^진도\s*(?:\(([^)]*)\))?\s*[:：]\s*(.+)$", line, flags=re.I)
        if progress_match:
            progress = normalize_counsel_duplicate_fingerprint_text(
                f"{progress_match.group(1) or ''}|{progress_match.group(2) or ''}"
            )
            in_homework = False
            continue
        if re.match(r"^숙제\s*[:：]?$", line):
            in_homework = True
            continue
        if is_counsel_homework_table_header(line):
            in_homework = True
            continue
        homework_item = parse_counsel_homework_fingerprint_line(line) if in_homework or "/" in line else None
        if homework_item and homework_item.get("book"):
            key = normalize_counsel_duplicate_fingerprint_text(homework_item.get("book"))
            if key:
                current = homework.setdefault(key, {"book": key, "progress": "", "next": "", "text": ""})
                if homework_item.get("progress"):
                    current["progress"] = normalize_counsel_duplicate_fingerprint_text(homework_item.get("progress"))
                if homework_item.get("next"):
                    current["next"] = normalize_counsel_duplicate_fingerprint_text(homework_item.get("next"))
                if homework_item.get("text"):
                    current["text"] = normalize_counsel_duplicate_fingerprint_text(homework_item.get("text"))
                continue
        note_text = extract_counsel_duplicate_note_text(line)
        if note_text:
            notes.append(note_text)
        in_homework = False

    homework_parts = []
    for key in sorted(homework):
        item = homework[key]
        homework_parts.append(
            ":".join(
                part
                for part in [
                    item["book"],
                    f"progress{item['progress']}" if item.get("progress") else "",
                    f"next{item['next']}" if item.get("next") else "",
                    f"text{item['text']}" if item.get("text") else "",
                ]
                if part
            )
        )
    parts = []
    if progress:
        parts.append(f"progress:{progress}")
    if notes:
        parts.append(f"notes:{normalize_counsel_duplicate_fingerprint_text(' '.join(notes))}")
    if homework_parts:
        parts.append(f"homework:{'|'.join(homework_parts)}")
    return "\n".join(parts)


def is_log_csv_duplicate_summary_fingerprint_usable(fingerprint: object) -> bool:
    text = str(fingerprint or "")
    return "progress:" in text and ("homework:" in text or "notes:" in text)


def log_csv_result_is_newly_added(result: dict[str, object]) -> bool:
    if is_truthy_result_value(result.get("newly_added")):
        return True
    status = str(result.get("result_status") or result.get("결과") or "").strip()
    return (
        status == "성공"
        and not log_csv_result_is_already_saved(result)
        and not google_docs_result_is_terminal_issue(result)
    )


def log_csv_duplicate_write_identity_key(result: dict[str, object]) -> tuple[str, str, str, str]:
    name = str(result.get("name") or "").strip()
    row_date = str(result.get("date") or "").strip()
    doc_id = str(result.get("doc_id") or result.get("doc_url") or "").strip()
    dedupe_key = str(result.get("dedupe_key") or "").strip()
    summary_fingerprint = str(result.get("summary_duplicate_fingerprint") or "").strip()
    if not summary_fingerprint:
        summary_fingerprint = build_log_csv_duplicate_summary_fingerprint(result.get("summary_preview"))
    summary_key = (
        summary_fingerprint
        if is_log_csv_duplicate_summary_fingerprint_usable(summary_fingerprint)
        else dedupe_key or normalize_log_csv_duplicate_text(str(result.get("summary_preview") or ""))
    )
    return (name, row_date, doc_id, summary_key)


def mark_log_csv_duplicate_write_suspicions(results: list[dict[str, object]]) -> None:
    buckets: dict[tuple[str, str, str, str], list[dict[str, object]]] = {}
    for result in results:
        if not log_csv_result_is_newly_added(result):
            continue
        key = log_csv_duplicate_write_identity_key(result)
        if not all(key):
            continue
        buckets.setdefault(key, []).append(result)

    for duplicates in buckets.values():
        if len(duplicates) < 2:
            continue
        for result in duplicates:
            result["duplicate_write_suspected"] = True
            result["manual_cleanup_recommended"] = True
            result["needs_review"] = True
            result["review_category"] = "duplicate_write_suspected"
            result["result_label"] = "중복 저장 의심"
            result["review_reason"] = add_google_docs_review_reason(
                str(result.get("review_reason") or ""),
                "같은 실행에서 같은 학생/날짜/문서/본문이 둘 이상 새로 저장된 것으로 보여 중복 저장 확인이 필요합니다.",
            )


def log_csv_duplicate_content_text(item: dict[str, object]) -> str:
    row = item["source_row"]
    preview_text = str(item.get("preview") or "").strip()
    if preview_text:
        return prepare_log_csv_preview_for_google_docs(item)

    content_parts = [
        normalize_log_notes_text(row.get(column))
        for column in LOG_EXPORT_CONTENT_COLUMNS
        if normalize_log_notes_text(row.get(column))
    ]
    return "\n".join(content_parts).strip()


def google_docs_book_from_log_row(row: dict[str, str]) -> str:
    for column in (
        "course",
        "book",
        "main_book",
        "curriculum_book",
        "textbook",
        "교재",
        "주교재",
        "program",
        "class",
        "class_name",
        "className",
        "homework_book",
    ):
        value = str(row.get(column) or "").strip()
        if value:
            return value
    return ""


def google_docs_book_from_log_item(item: dict[str, object]) -> str:
    row = item["source_row"]
    candidates = [google_docs_book_from_log_row(row)]
    applied_subject_info = item.get("applied_subject_info") or {}
    for key in (
        "csv_subject_value",
        "admin_subject",
        "admin_candidate_preview",
        "applied_subject",
    ):
        value = str(applied_subject_info.get(key) or "").strip()
        if value:
            candidates.append(value)
    preview_text = str(item.get("preview") or "").strip()
    if preview_text:
        candidates.append(preview_text)
    return "\n".join(candidate for candidate in candidates if candidate)


def admin_source_notes_for_log_item(item: dict[str, object]) -> str:
    row = item["source_row"]
    parts = [
        row.get("_admin_notes_original", ""),
        row.get("notes", ""),
        row.get("_consumed_admin_notes_removed_values", ""),
    ]
    return "\n".join(str(part or "") for part in parts if str(part or "").strip())


def prepare_log_csv_preview_for_google_docs(
    item: dict[str, object], *, consumed_filter_enabled: bool | None = None,
) -> str:
    row = item["source_row"]
    applied_subject_info = item.get("applied_subject_info") or {}
    subject = str(applied_subject_info.get("applied_subject") or row.get("curriculum") or "")
    prepared_text = prepare_summary_for_google_docs_cell(
        str(item.get("preview") or ""),
        subject=subject,
        student_name=str(row.get("name") or ""),
        curriculum=subject,
        book=google_docs_book_from_log_item(item),
        counseling_date=row.get("date") or row.get("counseling_date") or "",
    )
    filter_result = filter_consumed_admin_sections_from_docs_text(
        row.get("name"),
        row.get("admin_sid") or row.get("sid"),
        prepared_text,
        enabled=admin_consumed_filter_enabled() if consumed_filter_enabled is None else consumed_filter_enabled,
        admin_source_notes=admin_source_notes_for_log_item(item),
        counseling_date=row.get("date") or row.get("counseling_date"),
    )
    item["admin_consumed_docs_filter_diagnostic"] = filter_result.get("diagnostic", {})
    removed_notes = [str(note) for note in filter_result.get("removed_notes", [])]
    if removed_notes:
        item["admin_consumed_docs_removed_count"] = len(removed_notes)
        item["admin_consumed_docs_removed_preview"] = " | ".join(
            _admin_consumed_note_preview(note) for note in removed_notes[:3]
        )
    return str(filter_result.get("filtered_text") or prepared_text)


def google_docs_summary_progress_diagnostic(item: dict[str, object], docs_summary: str) -> str:
    if re.search(r"(?m)^진도(?:\([^)]*\))?\s*[:：]", str(docs_summary or "")):
        return ""
    row = item["source_row"]
    applied_subject_info = item.get("applied_subject_info") or {}
    subject = str(applied_subject_info.get("applied_subject") or row.get("curriculum") or "").strip()
    book_context = google_docs_book_from_log_item(item)
    reasons: list[str] = []
    if not str(row.get("progress") or "").strip():
        reasons.append("진도 차시 없음")
    if not subject:
        reasons.append("과목명 미확인")
    if not google_docs_primary_book_name(book_context):
        reasons.append("주교재 미확인")
    if str(row.get("homework_progress") or "").strip():
        reasons.append("진행률만 확인됨")
    return "; ".join(reasons) or "진도 줄 없음"


def log_csv_duplicate_selection_status(
    indexes: list[int],
    matched_rows: list[dict[str, object]],
    selected_index_set: set[int],
) -> str:
    statuses = []
    for index in indexes:
        if index in selected_index_set:
            statuses.append("선택됨")
        elif matched_rows[index].get("selected"):
            statuses.append("선택 가능")
        else:
            statuses.append("선택 불가")
    return ", ".join(dict.fromkeys(statuses))


def build_log_csv_duplicate_diagnostic_rows(
    matched_rows: list[dict[str, object]],
    selected_indices: list[int],
) -> list[dict[str, object]]:
    selected_index_set = set(selected_indices)
    diagnostic_rows: list[dict[str, object]] = []
    for group in find_log_csv_duplicate_groups(matched_rows):
        indexes = [int(index) for index in group["indexes"]]
        diagnostic_rows.append(
            {
                "name": group["name"],
                "date": group["date"],
                "원본 행 번호들": ", ".join(str(row_number) for row_number in group["row_numbers"]),
                "선택 상태": log_csv_duplicate_selection_status(indexes, matched_rows, selected_index_set),
                "내용 일부": group["content_preview"],
                "중복 판단 이유": "같은 학생/같은 날짜/정규화한 본문 동일",
            }
        )

    return diagnostic_rows


def find_log_csv_duplicate_groups(matched_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    duplicate_buckets: dict[tuple[str, str, str], list[tuple[int, dict[str, object], str]]] = {}

    for index, item in enumerate(matched_rows):
        row = item["source_row"]
        if not log_row_has_docs_content(row):
            continue
        date_text = str(row.get("date") or "").strip()
        name = str(row.get("name") or "").strip()
        if not date_text or not name:
            continue

        content_text = log_csv_duplicate_content_text(item)
        normalized_text = normalize_log_csv_duplicate_text(content_text)
        if not normalized_text:
            continue

        duplicate_buckets.setdefault((date_text, name, normalized_text), []).append((index, item, content_text))

    duplicate_groups: list[dict[str, object]] = []
    for (date_text, name, _normalized_text), candidates in duplicate_buckets.items():
        if len(candidates) < 2:
            continue
        indexes = [index for index, _item, _content_text in candidates]
        row_numbers = [str(item.get("row_index", "")) for _index, item, _content_text in candidates]
        duplicate_groups.append(
            {
                "indexes": indexes,
                "name": name,
                "date": date_text,
                "row_numbers": row_numbers,
                "content_preview": short_preview_text(candidates[0][2], max_length=220),
            }
        )

    return duplicate_groups


def auto_deselect_log_csv_duplicate_selection(
    matched_rows: list[dict[str, object]],
    selected_indices: list[int],
) -> tuple[list[int], list[dict[str, object]]]:
    selected_index_set = set(selected_indices)
    auto_deselected_rows: list[dict[str, object]] = []

    for group in find_log_csv_duplicate_groups(matched_rows):
        indexes = [int(index) for index in group["indexes"]]
        selected_group_indexes = [index for index in indexes if index in selected_index_set]
        selected_group_indexes.sort(key=lambda index: int(matched_rows[index].get("row_index") or 0))
        if len(selected_group_indexes) < 2:
            continue

        keep_index = selected_group_indexes[0]
        remove_indexes = selected_group_indexes[1:]
        for remove_index in remove_indexes:
            selected_index_set.discard(remove_index)

        auto_deselected_rows.append(
            {
                "name": group["name"],
                "date": group["date"],
                "유지한 원본 행 번호": matched_rows[keep_index].get("row_index", ""),
                "자동 해제한 원본 행 번호": ", ".join(
                    str(matched_rows[index].get("row_index", "")) for index in remove_indexes
                ),
                "내용 일부": group["content_preview"],
                "중복 판단 이유": "같은 학생/같은 날짜/정규화한 본문 동일",
            }
        )

    corrected_indices = [index for index in selected_indices if index in selected_index_set]
    return corrected_indices, auto_deselected_rows


def record_csv_worker_failure(error: BaseException, initial_state: LogCsvBatchState) -> None:
    state = load_log_csv_batch_state(LOG_CSV_DOCS_STATE_PATH)
    if state is None or state.get("run_id") != initial_state.get("run_id"):
        state = initial_state
    save_log_csv_batch_state(
        LOG_CSV_DOCS_STATE_PATH,
        interrupt_log_csv_batch_state(
            state, error=str(error)[:500], updated_at=log_csv_batch_now_iso(),
        ),
    )


def execute_log_csv_transfer(
    *, batch_pause_seconds, completed_before, consumed_filter_enabled,
    history_entry, lock_retry_pause_seconds, matched_rows, process_selected_indices,
    processing_history, results, retry_attempt, retry_of_run_id, retry_run_active,
    retry_source_status_by_index, row_pause_seconds, run_scope_indices, run_state,
    save_batch_size, source_csv_name, source_csv_size, web_app_url,
) -> None:

    def persist_batch(state):
        save_log_csv_batch_state(LOG_CSV_DOCS_STATE_PATH, state)
        return True

    def persist_history(history):
        # Only this run's entry belongs to the worker's snapshot.
        latest = load_log_csv_processing_history(LOG_CSV_PROCESSING_HISTORY_PATH)
        latest = upsert_log_csv_history_entry(latest, history_entry)
        save_log_csv_processing_history(LOG_CSV_PROCESSING_HISTORY_PATH, latest)
        return True

    persist_batch(run_state)
    persist_history(processing_history)
    total_count = len(run_scope_indices)
    batch_total = (total_count + save_batch_size - 1) // save_batch_size if total_count else 0
    initial_classifications = [classify_google_docs_save_result(result) for result in results]
    success_count = sum((1 for classification in initial_classifications if classification.get('count_key') == 'new_saved'))
    already_saved_count = sum((1 for classification in initial_classifications if classification.get('count_key') == 'already_saved'))
    updated_existing_count = sum((1 for classification in initial_classifications if classification.get('count_key') == 'updated_existing'))
    saved_confirmed_after_timeout_count = sum((1 for classification in initial_classifications if classification.get('count_key') == 'saved_confirmed_after_timeout'))
    failure_count = sum((1 for classification in initial_classifications if classification.get('count_key') == 'failed'))
    lock_timeout_count = sum((1 for classification in initial_classifications if classification.get('count_key') == 'lock_timeout_retryable'))
    timeout_unknown_count = sum((1 for classification in initial_classifications if classification.get('count_key') == 'timeout_unknown'))
    needs_review_count = sum((1 for classification in initial_classifications if classification.get('count_key') in {'needs_review', 'duplicate_write_suspected'}))
    added_row_count = sum((1 for result in results if is_truthy_result_value(result.get('added_row'))))
    failed_indices = list(run_state.get('failed_indices') or [])

    def checkpoint_docs_save_result(selected_index: int) -> None:
        nonlocal history_entry, processing_history, run_state
        if not results:
            return
        updated_at = log_csv_batch_now_iso()
        run_state = record_log_csv_batch_result(run_state, source_index=selected_index, result=results[-1], failed=selected_index in failed_indices, updated_at=updated_at)
        persist_batch(run_state)
        history_outcome = log_csv_history_outcome_from_result(results[-1])
        for source_index in log_csv_history_source_indices(matched_rows[selected_index]):
            history_entry = record_log_csv_history_outcome(history_entry, LogCsvHistoryUpdate(source_index=source_index, outcome=history_outcome, updated_at=updated_at))
        processing_history = upsert_log_csv_history_entry(processing_history, history_entry)
        if not persist_history(processing_history):
            raise WorkflowStepError('로그 처리 이력 저장', '처리 이력을 저장하지 못해 남은 Docs 전송을 중지했습니다.')
    docs_post_request = None
    docs_posted_row_count = 0

    def docs_target_key(selected_index):
        docs_matches = matched_rows[selected_index].get('docs_matches') or []
        if len(docs_matches) == 1:
            doc_id = str(docs_matches[0].get('doc_id') or '').strip()
            if doc_id:
                return f"doc:{doc_id}"
        return f"source:{selected_index}"

    def send_docs_request(request_data, verify_only):
        try:
            if verify_only:
                raise GoogleDocsPostIndeterminateError(
                    "Google Docs 저장 확인",
                    "이전 실행의 전송 결과를 먼저 확인합니다.",
                )
            try:
                result = append_summary_to_google_doc(
                    web_app_url,
                    request_data["student_name"],
                    parse_log_export_date(request_data["counseling_date"]),
                    request_data["summary"],
                    request_data["doc_id"],
                    dedupe_key=request_data["dedupe_key"],
                    subject=request_data["subject"],
                    book=request_data["book"],
                    post_request=docs_post_request,
                )
            except GoogleDocsLockTimeoutError:
                time.sleep(lock_retry_pause_seconds)
                result = append_summary_to_google_doc(
                    web_app_url,
                    request_data["student_name"],
                    parse_log_export_date(request_data["counseling_date"]),
                    request_data["summary"],
                    request_data["doc_id"],
                    dedupe_key=request_data["dedupe_key"],
                    subject=request_data["subject"],
                    book=request_data["book"],
                    post_request=docs_post_request,
                )
            return result, None, None
        except (GoogleDocsPostTimeoutError, GoogleDocsPostIndeterminateError, GoogleDocsLockTimeoutError) as exc:
            time.sleep(GOOGLE_DOCS_TIMEOUT_VERIFY_PAUSE_SECONDS)
            verification = verify_summary_in_google_doc_after_timeout(
                web_app_url,
                request_data["student_name"],
                parse_log_export_date(request_data["counseling_date"]),
                request_data["summary"],
                request_data["doc_id"],
                dedupe_key=request_data["dedupe_key"],
                subject=request_data["subject"],
                book=request_data["book"],
                post_request=docs_post_request,
            )
            return None, verification, exc

    def parallel_transfers():
        nonlocal docs_posted_row_count, failure_count, run_state
        pending_indices = list(process_selected_indices)
        remaining_position_by_index = {
            selected_index: remaining_position
            for remaining_position, selected_index in enumerate(pending_indices, start=1)
        }
        completed_post_count = 0
        with ThreadPoolExecutor(
            max_workers=LOG_CSV_TO_DOCS_MAX_WORKERS,
            thread_name_prefix="connect-log-docs",
        ) as executor:
            active = {}
            while pending_indices or active:
                target_keys = {docs_target_key(transfer["selected_index"]) for transfer in active.values()}
                wave = []
                deferred = []
                for selected_index in pending_indices:
                    target_key = docs_target_key(selected_index)
                    if len(wave) + len(active) < LOG_CSV_TO_DOCS_MAX_WORKERS and target_key not in target_keys:
                        wave.append(selected_index)
                        target_keys.add(target_key)
                    else:
                        deferred.append(selected_index)
                pending_indices = deferred
                prepared = []
                for selected_index in wave:
                    remaining_position = remaining_position_by_index[selected_index]
                    position = completed_before + remaining_position
                    batch_index = (position - 1) // save_batch_size + 1
                    current_batch_size = min(
                        save_batch_size - (position - 1) % save_batch_size,
                        len(process_selected_indices) - remaining_position + 1,
                    )
                    item = matched_rows[selected_index]
                    row = item['source_row']
                    applied_subject_info = item.get('applied_subject_info') or {}
                    admin_match_log_fields = log_csv_admin_match_log_fields(item)
                    docs_matches = item.get('docs_matches') or []
                    name = str(row.get('name') or '').strip()
                    date_text = str(row.get('date') or '').strip()
                    final_docs_summary = prepare_log_csv_preview_for_google_docs(
                        item,
                        consumed_filter_enabled=consumed_filter_enabled,
                    )
                    retry_source_status = str(
                        retry_source_status_by_index.get(str(selected_index), '')
                    )
                    if not final_docs_summary:
                        failure_count += 1
                        results.append(build_log_csv_to_docs_result_entry(row, '실패', '추가할 본문이 없습니다.', source_index=selected_index, retry_of_run_id=retry_of_run_id, retry_attempt=retry_attempt if retry_run_active else '', retry_source_status=retry_source_status, batch_index=batch_index, batch_size=current_batch_size, batch_total=batch_total, **admin_match_log_fields))
                        failed_indices.append(selected_index)
                        checkpoint_docs_save_result(selected_index)
                        continue
                    if len(docs_matches) != 1:
                        failure_count += 1
                        results.append(build_log_csv_to_docs_result_entry(row, '실패', 'Google Docs 매핑이 없거나 중복입니다.', source_index=selected_index, retry_of_run_id=retry_of_run_id, retry_attempt=retry_attempt if retry_run_active else '', retry_source_status=retry_source_status, batch_index=batch_index, batch_size=current_batch_size, batch_total=batch_total, **admin_match_log_fields, summary_text=final_docs_summary))
                        failed_indices.append(selected_index)
                        checkpoint_docs_save_result(selected_index)
                        continue
                    docs_match = docs_matches[0]
                    doc_id = str(docs_match.get('doc_id') or '').strip()
                    doc_url = str(docs_match.get('doc_url') or '').strip()
                    dedupe_key = build_log_csv_dedupe_key(row)
                    request_data = {
                        "student_name": name,
                        "counseling_date": date_text,
                        "summary": final_docs_summary,
                        "doc_id": doc_id,
                        "dedupe_key": dedupe_key,
                        "subject": str(applied_subject_info.get('applied_subject') or row.get('curriculum') or ''),
                        "book": google_docs_book_from_log_item(item),
                    }
                    prepared.append({
                        "selected_index": selected_index,
                        "position": position,
                        "batch_index": batch_index,
                        "current_batch_size": current_batch_size,
                        "item": item,
                        "row": row,
                        "applied_subject_info": applied_subject_info,
                        "admin_match_log_fields": admin_match_log_fields,
                        "name": name,
                        "final_docs_summary": final_docs_summary,
                        "retry_source_status": retry_source_status,
                        "doc_id": doc_id,
                        "doc_url": doc_url,
                        "dedupe_key": dedupe_key,
                        "request_data": request_data,
                    })
                if not prepared and not active:
                    continue

                previous_requests = {
                    int(request["source_index"]): request
                    for request in log_csv_batch_in_flight_requests(run_state)
                }
                new_in_flight = []
                for transfer in prepared:
                    selected_index = transfer["selected_index"]
                    previous_request = previous_requests.get(selected_index)
                    transfer["verify_only"] = previous_request is not None
                    if previous_request is not None:
                        transfer["request_data"] = dict(
                            previous_request.get("request") or transfer["request_data"]
                        )
                    else:
                        new_in_flight.append({
                            "source_index": selected_index,
                            "request": transfer["request_data"],
                        })

                if prepared:
                    first_transfer = prepared[0]
                    run_state = set_log_csv_batch_phase(
                        run_state,
                        current_position=first_transfer["position"],
                        current_name=first_transfer["name"],
                        phase='Google Docs 응답 대기 중',
                        updated_at=log_csv_batch_now_iso(),
                    )
                    run_state = set_log_csv_batch_in_flight_requests(
                        run_state,
                        requests=[*previous_requests.values(), *new_in_flight],
                        updated_at=log_csv_batch_now_iso(),
                    )
                    persist_batch(run_state)
                    docs_posted_row_count += len(new_in_flight)
                for transfer in prepared:
                    future = executor.submit(
                        send_docs_request,
                        transfer["request_data"],
                        transfer["verify_only"],
                    )
                    active[future] = transfer
                done, _waiting = wait(active, return_when=FIRST_COMPLETED)
                future = min(done, key=lambda item: active[item]["selected_index"])
                transfer = active[future]
                yield transfer, future
                del active[future]
                if not transfer["verify_only"]:
                    completed_post_count += 1
                    if pending_indices and completed_post_count % save_batch_size == 0:
                        time.sleep(batch_pause_seconds)
                    elif pending_indices and completed_post_count % LOG_CSV_TO_DOCS_MAX_WORKERS == 0:
                        time.sleep(row_pause_seconds)

    try:
        for transfer, result_future in parallel_transfers():
            selected_index = transfer["selected_index"]
            position = transfer["position"]
            batch_index = transfer["batch_index"]
            current_batch_size = transfer["current_batch_size"]
            row = transfer["row"]
            admin_match_log_fields = transfer["admin_match_log_fields"]
            final_docs_summary = transfer["final_docs_summary"]
            retry_source_status = transfer["retry_source_status"]
            doc_id = transfer["doc_id"]
            doc_url = transfer["doc_url"]
            dedupe_key = transfer["dedupe_key"]
            request_data = transfer["request_data"]
            try:
                result, verify_result, transfer_error = result_future.result()
                if transfer_error is not None:
                    raise transfer_error
                result_message = str(result.get('message') or '')
                duplicate_reason = str(result.get('duplicate_reason') or '')
                dedupe_key_stale = result.get('dedupe_key_stale', '')
                updated_existing = result.get('updated_existing') is True or result_message in {'updated_existing_progress_only', 'updated_existing_format_only', 'updated_existing_same_date'}
                duplicate_skipped = result.get('duplicate_skipped') is True or result_message.startswith('duplicate_skipped') or result_message == 'similar_record_needs_review'
                normalized_duplicate_skipped = duplicate_skipped and (duplicate_reason == 'normalized_content' or result_message == 'duplicate_skipped_by_normalized_content')
                status = '기존 기록 업데이트' if updated_existing else '중복으로 추가하지 않음' if duplicate_skipped else '성공'
                if updated_existing:
                    updated_existing_count += 1
                elif status == '중복으로 추가하지 않음':
                    already_saved_count += 1
                else:
                    success_count += 1
                added_row = result.get('added_row', '')
                copied_row_template = result.get('copied_row_template', '')
                style_template_used = result.get('style_template_used', '')
                style_template_source = result.get('style_template_source', '')
                max_cell_count = result.get('max_cell_count', '')
                needs_review = result.get('needs_review', False)
                review_reason = str(result.get('review_reason') or '')
                added_row_style_success = google_docs_added_row_style_success(result)
                stale_dedupe_needs_review = is_truthy_result_value(dedupe_key_stale)
                added_row_needs_review = is_truthy_result_value(added_row)
                copied_row_template_failed = added_row_needs_review and is_false_result_value(copied_row_template)
                style_template_failed = is_false_result_value(style_template_used)
                style_source_fallback = google_docs_style_source_needs_review(style_template_source)
                review_candidate = not duplicate_skipped and (not added_row_style_success) and (is_truthy_result_value(needs_review) or stale_dedupe_needs_review or google_docs_review_reason_needs_review(review_reason) or copied_row_template_failed or style_template_failed or (added_row_needs_review and (not str(copied_row_template or '').strip())) or (added_row_needs_review and (not str(style_template_used or '').strip())) or (added_row_needs_review and (not str(style_template_source or '').strip())) or style_source_fallback)
                review_category = 'needs_review' if review_candidate else ''
                if stale_dedupe_needs_review:
                    review_category = 'stale_dedupe_key_review'
                if added_row_needs_review:
                    added_row_count += 1
                    if not added_row_style_success and (not stale_dedupe_needs_review):
                        review_category = 'added_row_review'
                        review_reason = add_google_docs_review_reason(review_reason, '새 행이 생성되었습니다. 위 행과 양식이 같은지 확인하세요.')
                if copied_row_template_failed:
                    review_category = 'new_row_style_review'
                    review_reason = add_google_docs_review_reason(review_reason, '행 템플릿 복사에 실패해 fallback 행을 사용했습니다.')
                if style_source_fallback:
                    review_category = 'style_fallback_review'
                    review_reason = add_google_docs_review_reason(review_reason, '상담 내용 칸 서식을 찾지 못해 fallback 서식을 사용했습니다.')
                if style_template_failed:
                    review_category = 'style_fallback_review'
                    review_reason = add_google_docs_review_reason(review_reason, '상담 내용 칸 서식을 적용하지 못했습니다.')
                if normalized_duplicate_skipped:
                    review_candidate = True
                    review_category = 'already_saved_format_diff'
                    review_reason = add_google_docs_review_reason(review_reason, '문서 안에 포맷만 다른 유사 기록이 있어 새로 추가하지 않았습니다. 필요하면 기존 기록을 수동으로 정리하세요.')
                if updated_existing:
                    review_candidate = False
                    format_only_update = duplicate_reason == 'format_only_update' or result_message == 'updated_existing_format_only'
                    same_date_replaced = duplicate_reason == 'same_date_replaced' or result_message == 'updated_existing_same_date'
                    review_category = 'updated_existing_same_date' if same_date_replaced else 'updated_existing_format_only' if format_only_update else 'updated_existing_progress_only'
                    review_reason = add_google_docs_review_reason(review_reason, '기존 같은 날짜 기록을 새 기록으로 교체했습니다.' if same_date_replaced else '기존 같은 날짜 기록과 내용은 같고 포맷만 달라 새 정돈 포맷으로 업데이트했습니다.' if format_only_update else '기존 같은 날짜 기록의 진도 줄만 달라 새 기록으로 업데이트했습니다.')
                if duplicate_reason == 'unsafe_similar_record' or result_message == 'similar_record_needs_review':
                    review_candidate = True
                    review_category = 'unsafe_similar_record'
                    review_reason = add_google_docs_review_reason(review_reason, '문서 안에 같은 날짜의 유사 기록이 있으나 진도 외의 차이가 있어 자동 업데이트하지 않았습니다. 기존 기록과 새 기록을 확인하세요.')
                if review_candidate:
                    needs_review_count += 1
                results.append(build_log_csv_to_docs_result_entry(row, status, str(result_message or result.get('status') or '-'), doc_url=doc_url, doc_id=doc_id, dedupe_key=dedupe_key, duplicate_reason=duplicate_reason, dedupe_key_stale=dedupe_key_stale, updated_existing=updated_existing, already_saved_no_write=duplicate_skipped, duplicate_skipped=duplicate_skipped, skipped_no_write=duplicate_skipped, newly_added=not duplicate_skipped and (not updated_existing), result_label='기존 기록 업데이트 · 포맷 정리' if updated_existing and (duplicate_reason == 'format_only_update' or result_message == 'updated_existing_format_only') else '기존 기록 업데이트' if updated_existing else '유사 기록 확인 필요' if duplicate_reason == 'unsafe_similar_record' or result_message == 'similar_record_needs_review' else '이미 저장됨 · 포맷 차이 감지' if normalized_duplicate_skipped else '이미 저장됨' if duplicate_skipped else '신규 저장', response_row=result.get('row', ''), response_col=result.get('col', ''), added_row=added_row, copied_row_template=copied_row_template, style_template_used=style_template_used, style_template_source=style_template_source, max_cell_count=max_cell_count, needs_review=review_candidate, review_reason=review_reason, review_category=review_category, source_index=selected_index, retry_of_run_id=retry_of_run_id, retry_attempt=retry_attempt if retry_run_active else '', retry_source_status=retry_source_status, batch_index=batch_index, batch_size=current_batch_size, batch_total=batch_total, **admin_match_log_fields, summary_text=final_docs_summary))
            except (GoogleDocsPostTimeoutError, GoogleDocsPostIndeterminateError, GoogleDocsLockTimeoutError) as exc:
                verify_classification = classify_google_docs_timeout_verification(verify_result)
                verify_status = str(verify_classification.get('status') or 'timeout_unknown')
                verify_message = str(verify_classification.get('review_reason') or '') or str(verify_result.get('message') or '') or str(exc)
                if verify_status == 'saved_confirmed_after_timeout':
                    saved_confirmed_after_timeout_count += 1
                elif is_truthy_result_value(verify_classification.get('failure')):
                    failure_count += 1
                    failed_indices.append(selected_index)
                elif verify_status == 'lock_timeout_retryable':
                    lock_timeout_count += 1
                    failed_indices.append(selected_index)
                else:
                    timeout_unknown_count += 1
                if is_truthy_result_value(verify_classification.get('needs_review')):
                    needs_review_count += 1
                results.append(build_log_csv_to_docs_result_entry(row, verify_status, verify_message[:500], doc_url=doc_url, doc_id=doc_id, dedupe_key=dedupe_key, response_row=verify_result.get('row', ''), response_col=verify_result.get('col', ''), needs_review=verify_classification.get('needs_review', False), review_reason=verify_classification.get('review_reason', verify_message), timeout_unknown=verify_classification.get('timeout_unknown', False), retry_recommended=verify_classification.get('retry_recommended', False), review_category=verify_classification.get('review_category', verify_status), result_label=verify_classification.get('user_label', verify_status), source_index=selected_index, retry_of_run_id=retry_of_run_id, retry_attempt=retry_attempt if retry_run_active else '', retry_source_status=retry_source_status, batch_index=batch_index, batch_size=current_batch_size, batch_total=batch_total, **admin_match_log_fields, summary_text=final_docs_summary))
            except WorkflowStepError as exc:
                failure_count += 1
                results.append(build_log_csv_to_docs_result_entry(row, '실패', str(exc)[:500], doc_url=doc_url, doc_id=doc_id, dedupe_key=dedupe_key, source_index=selected_index, retry_of_run_id=retry_of_run_id, retry_attempt=retry_attempt if retry_run_active else '', retry_source_status=retry_source_status, batch_index=batch_index, batch_size=current_batch_size, batch_total=batch_total, **admin_match_log_fields, summary_text=final_docs_summary))
                failed_indices.append(selected_index)
            if consumed_filter_enabled and results and (str(results[-1].get('source_index') or '') == str(selected_index)):
                mark_result = mark_consumed_admin_notes_for_saved_row(row, results[-1], final_docs_summary)
                if mark_result.get('marked_count'):
                    results[-1]['admin_consumed_marked_count'] = mark_result.get('marked_count', 0)
                if mark_result.get('error'):
                    results[-1]['admin_consumed_mark_error'] = mark_result.get('error', '')
            checkpoint_docs_save_result(selected_index)
        mark_log_csv_duplicate_write_suspicions(results)
        final_result_classifications = [classify_google_docs_save_result(result) for result in results]
        history_updated_at = log_csv_batch_now_iso()
        for result in results:
            try:
                matched_index = int(result.get('source_index'))
            except (TypeError, ValueError):
                continue
            if matched_index < 0 or matched_index >= len(matched_rows):
                continue
            history_outcome = log_csv_history_outcome_from_result(result)
            for source_index in log_csv_history_source_indices(matched_rows[matched_index]):
                history_entry = record_log_csv_history_outcome(history_entry, LogCsvHistoryUpdate(source_index=source_index, outcome=history_outcome, updated_at=history_updated_at))
        history_entry = finalize_log_csv_history_entry(history_entry, history_updated_at)
        processing_history = upsert_log_csv_history_entry(processing_history, history_entry)
        if not persist_history(processing_history):
            raise WorkflowStepError('로그 처리 이력 저장', '최종 처리 이력을 저장하지 못했습니다.')
        success_count = sum((1 for classification in final_result_classifications if classification.get('count_key') == 'new_saved'))
        already_saved_count = sum((1 for classification in final_result_classifications if classification.get('count_key') == 'already_saved'))
        updated_existing_count = sum((1 for classification in final_result_classifications if classification.get('count_key') == 'updated_existing'))
        saved_confirmed_after_timeout_count = sum((1 for classification in final_result_classifications if classification.get('count_key') == 'saved_confirmed_after_timeout'))
        failure_count = sum((1 for classification in final_result_classifications if classification.get('count_key') == 'failed'))
        lock_timeout_count = sum((1 for classification in final_result_classifications if classification.get('count_key') == 'lock_timeout_retryable'))
        timeout_unknown_count = sum((1 for classification in final_result_classifications if classification.get('count_key') == 'timeout_unknown'))
        duplicate_write_suspected_count = sum((1 for classification in final_result_classifications if classification.get('count_key') == 'duplicate_write_suspected'))
        document_review_count = sum((1 for classification in final_result_classifications if classification.get('count_key') == 'needs_review'))
        has_retry_issue = bool(failure_count or lock_timeout_count or timeout_unknown_count or duplicate_write_suspected_count)
        result_log_path = save_log_csv_to_docs_result_log(
            results, str(source_csv_name), source_csv_size,
            run_id=str(run_state.get('run_id') or ''),
        )
        result_log_path_text = str(result_log_path)
        run_state['results'] = [dict(result) for result in results]
        run_state['failed_indices'] = list(failed_indices)
        run_state = complete_log_csv_batch_state(run_state, result_log_path=result_log_path_text, updated_at=log_csv_batch_now_iso())
        persist_batch(run_state)
    except Exception as exc:
        history_entry = finalize_log_csv_history_entry(history_entry, log_csv_batch_now_iso())
        processing_history = upsert_log_csv_history_entry(processing_history, history_entry)
        history_saved = persist_history(processing_history)
        run_state = interrupt_log_csv_batch_state(run_state, error=str(exc)[:500], updated_at=log_csv_batch_now_iso())
        persist_batch(run_state)
        completed_count = len(run_state.get('completed_indices') or [])
        remaining_count = max(0, int(run_state.get('total') or 0) - completed_count)
@st.fragment
def render_log_csv_to_docs_preview_ui(
    history_summary_placeholder,
    pending_admin_sync: AdminNotesSyncRequest | None = None,
) -> None:
    release_stale_log_csv_session_lock()
    restore_log_csv_auto_import_from_batch_state()
    render_log_csv_auto_import_watcher()
    auto_imported_log_csv = active_auto_import_log_csv()
    worker_running = csv_worker_is_running(LOG_CSV_WORKER_LOCK_PATH)
    recovery_state = load_current_log_csv_batch_state()
    if worker_running:
        st.session_state["log_csv_worker_progress_open"] = True
    with st.expander(
        "로그 CSV → 학생 Docs 추가",
        expanded=(
            auto_imported_log_csv is not None
            or bool(st.session_state.get("log_csv_worker_progress_open"))
            or bool(st.session_state.get("log_csv_workspace_has_upload"))
            or log_csv_completion_is_pending(recovery_state)
            or (recovery_state is not None and recovery_state.get("status") in {"running", "interrupted"})
        ),
    ):
        loaded_processing_history = load_current_log_csv_processing_history()
        history_processing_blocked = loaded_processing_history is None
        processing_history = (
            loaded_processing_history
            if loaded_processing_history is not None
            else empty_log_csv_processing_history()
        )
        if not history_processing_blocked:
            history_summary_placeholder = refresh_log_csv_processing_history_summary(
                history_summary_placeholder,
                processing_history,
            )
        history_error = str(st.session_state.get("log_csv_processing_history_error") or "")
        if history_error:
            st.error(
                "로그 처리 이력을 읽거나 저장할 수 없어 안전을 위해 Docs 전송을 중지했습니다. "
                "이력 파일을 복구한 뒤 다시 시도하세요."
            )
            st.caption(short_preview_text(history_error, 160))
        manually_uploaded_log_csv = st.file_uploader(
            "admin 로그 내보내기 CSV",
            type=["csv"],
            accept_multiple_files=False,
            key="log_csv_to_docs_preview_upload_v2",
            disabled=worker_running,
            on_change=handle_log_csv_preview_upload_change,
        )
        st.session_state["log_csv_workspace_has_upload"] = manually_uploaded_log_csv is not None
        save_button_slot = st.empty()
        if worker_running:
            active_state = load_current_log_csv_batch_state()
            if active_state is not None:
                st.session_state["log_csv_observed_worker_run"] = active_state.get("run_id")
                render_log_csv_persisted_progress(active_state["upload_signature"])
            return
        uploaded_log_csv = manually_uploaded_log_csv or auto_imported_log_csv
        if uploaded_log_csv is None:
            recovery_state = load_current_log_csv_batch_state()
            if recovery_state is not None and log_csv_completion_is_pending(recovery_state):
                render_log_csv_recovered_completion(recovery_state)
            elif recovery_state is not None and recovery_state.get("status") in {"running", "interrupted"}:
                render_log_csv_persisted_progress(recovery_state["upload_signature"])
                st.caption("이어 저장할 CSV 원본이 선택되지 않았습니다.")
            if not st.session_state.get("log_csv_to_docs_is_saving", False):
                save_button_slot.button(
                    "선택한 기록을 학생 Docs에 추가", disabled=True, key="log_csv_to_docs_save_button"
                )
            return

        try:
            uploaded_log_csv_bytes = bytes(uploaded_log_csv.getvalue())
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            st.error(f"CSV 원본을 읽는 중 오류가 발생했습니다: {exc}")
            return
        csv_content_hash = csv_bytes_sha256(uploaded_log_csv_bytes)

        try:
            log_rows, columns, encoding = read_log_export_csv(uploaded_log_csv)
        except WorkflowStepError as exc:
            st.error(str(exc))
            return
        except Exception as exc:
            st.error(f"CSV를 읽는 중 오류가 발생했습니다: {exc}")
            return

        missing_columns = validate_log_export_columns(columns)
        if missing_columns:
            st.error(f"필수 컬럼이 없습니다: {', '.join(missing_columns)}")
            st.caption(f"감지된 컬럼: {', '.join(columns) if columns else '-'}")
            return
        if not log_rows:
            st.warning("CSV에 읽을 수 있는 행이 없습니다.")
            return
        original_log_rows = [dict(row) for row in log_rows]
        original_row_hashes = fingerprint_log_csv_rows(original_log_rows)
        history_source_date = log_csv_source_date_from_rows(original_log_rows)

        consumed_filter_diagnostic: dict[str, object] = {
            "enabled": admin_consumed_filter_enabled(),
            "removed_count": 0,
            "removed_rows": [],
        }
        log_rows, consumed_filter_diagnostic = filter_consumed_admin_notes_from_log_rows(
            log_rows,
            enabled=admin_consumed_filter_enabled(),
        )
        pending_admin_sync_match_count = apply_pending_admin_notes_to_log_rows(
            log_rows,
            pending_admin_sync,
        )
        csv_content_hash = effective_log_csv_content_hash(
            csv_content_hash,
            pending_admin_sync,
            pending_admin_sync_match_count,
        )

        docs_rows = load_google_docs_map_rows()
        admin_rows = load_admin_students_from_api(load_admin_api_base_url())
        admin_lookup_error = str(st.session_state.get("last_admin_students_lookup_error") or "")
        matched_rows = match_log_rows_to_docs(log_rows, docs_rows)
        matched_rows = merge_homework_duplicate_log_rows(matched_rows)
        pending_admin_sync_indices = {
            index
            for index, item in enumerate(matched_rows)
            if pending_admin_sync is not None
            and str((item.get("source_row") or {}).get("_pending_admin_notes_sync_key") or "")
            == pending_admin_sync.preview_key
        }
        for item in matched_rows:
            row = item["source_row"]
            csv_subject_info = csv_subject_from_log_row(row)
            subject_hint = str(csv_subject_info.get("subject") or "")
            admin_subject_info = infer_admin_subject_for_log_row(
                row,
                admin_rows,
                admin_lookup_error,
                subject_hint=subject_hint,
            )
            item["admin_subject_info"] = admin_subject_info
            item["applied_subject_info"] = build_applied_subject_info(
                row,
                admin_subject_info,
            )
            item["lesson_type_info"] = infer_lesson_type_for_log_item(row, admin_subject_info)
            docs_preview_text = prepare_log_csv_preview_for_google_docs(item).strip()
            item["docs_preview_text"] = docs_preview_text
            item["docs_progress_diagnostic"] = google_docs_summary_progress_diagnostic(item, docs_preview_text)
            if not docs_preview_text:
                item["selected"] = False
                item["status"] = "기록 내용 없음"
        special_class_diagnostic_rows = build_special_class_diagnostic_rows(matched_rows, missing_columns)
        lesson_type_diagnostic_rows = build_lesson_type_diagnostic_rows(matched_rows)
        docs_progress_diagnostic_rows = [
            {
                "원본 행": item.get("row_index", ""),
                "학생명": (item.get("source_row") or {}).get("name", ""),
                "상담일": (item.get("source_row") or {}).get("date", ""),
                "진도 진단": item.get("docs_progress_diagnostic", ""),
                "전송 본문 일부": short_preview_text(str(item.get("docs_preview_text") or ""), 160),
            }
            for item in matched_rows
            if str(item.get("docs_progress_diagnostic") or "").strip()
        ]
        lesson_type_counts = {"정규강의": 0, "특강": 0, "미확인": 0, "확인 필요": 0}
        admin_match_counts: dict[str, int] = {}
        for item in matched_rows:
            lesson_type = str((item.get("lesson_type_info") or {}).get("lesson_type") or "미확인")
            lesson_type_counts[lesson_type] = lesson_type_counts.get(lesson_type, 0) + 1
            admin_status = str((item.get("applied_subject_info") or {}).get("admin_status") or "미확인")
            admin_match_counts[admin_status] = admin_match_counts.get(admin_status, 0) + 1
        show_developer_diagnostics = bool(st.session_state.get("show_developer_diagnostics", False))
        selectable_indices = [
            index
            for index, item in enumerate(matched_rows)
            if item.get("selected")
        ]
        selectable_index_set = set(selectable_indices)
        save_excluded_items = [
            item
            for index, item in enumerate(matched_rows)
            if index not in selectable_index_set
        ]
        save_excluded_count = len(save_excluded_items)
        history_selectable_source_indices = log_csv_history_source_indices_for_matches(
            matched_rows,
            selectable_indices,
        )
        history_review_source_indices = log_csv_history_review_source_indices(
            matched_rows,
            selectable_indices,
        )
        history_entry = find_log_csv_history_entry(processing_history, csv_content_hash)
        if history_entry is None:
            new_row_count = count_new_log_csv_rows(
                processing_history,
                csv_content_hash,
                original_row_hashes,
            )
            history_entry = prepare_log_csv_history_entry(
                processing_history,
                LogCsvHistoryStart(
                    csv_hash=csv_content_hash,
                    row_hashes=tuple(original_row_hashes),
                    selected_indices=tuple(history_selectable_source_indices),
                    review_indices=tuple(history_review_source_indices),
                    started_at=log_csv_batch_now_iso(),
                    new_row_count=new_row_count,
                    source_date=history_source_date,
                ),
            )
            processing_history = upsert_log_csv_history_entry(processing_history, history_entry)
            if not history_processing_blocked and not persist_current_log_csv_processing_history(
                processing_history
            ):
                history_processing_blocked = True
                st.error(
                    "로그 처리 이력을 저장할 수 없어 안전을 위해 Docs 전송을 중지했습니다."
                )
        else:
            recoverable_source_indices = set(history_selectable_source_indices) | set(
                history_review_source_indices
            )
            legacy_excluded_source_indices = {
                index
                for index, outcome in enumerate(history_entry["outcomes"])
                if outcome == "excluded" and index in recoverable_source_indices
            }
            if legacy_excluded_source_indices:
                history_entry = prepare_log_csv_history_entry(
                    processing_history,
                    LogCsvHistoryStart(
                        csv_hash=csv_content_hash,
                        row_hashes=tuple(original_row_hashes),
                        selected_indices=tuple(history_selectable_source_indices),
                        review_indices=tuple(history_review_source_indices),
                        started_at=log_csv_batch_now_iso(),
                        new_row_count=int(history_entry.get("new_row_count") or 0),
                        source_date=history_source_date,
                    ),
                )
                processing_history = upsert_log_csv_history_entry(
                    processing_history,
                    history_entry,
                )
                if not history_processing_blocked and not persist_current_log_csv_processing_history(
                    processing_history
                ):
                    history_processing_blocked = True
                    st.error(
                        "기존 로그 처리 이력을 복구해 저장할 수 없어 Docs 전송을 중지했습니다."
                    )
        history_completed = log_csv_history_is_completed(history_entry)
        unresolved_history_source_indices = set(unresolved_log_csv_history_indices(history_entry))
        history_default_selectable_indices = [
            matched_index
            for matched_index in selectable_indices
            if unresolved_history_source_indices.intersection(
                log_csv_history_source_indices(matched_rows[matched_index])
            )
        ]
        if not history_processing_blocked:
            refresh_log_csv_processing_history_summary(history_summary_placeholder, processing_history)
        upload_content_signature = hashlib.sha256(
            json.dumps(log_rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        upload_signature = "|".join(
            [
                str(getattr(uploaded_log_csv, "name", "")),
                str(getattr(uploaded_log_csv, "size", "")),
                str(len(log_rows)),
                upload_content_signature,
            ]
        )
        upload_signature_hash = hashlib.sha256(upload_signature.encode("utf-8")).hexdigest()[:12]
        auto_export_token = ""
        if manually_uploaded_log_csv is None and auto_imported_log_csv is not None:
            auto_export_token = log_csv_auto_export_token(
                auto_imported_log_csv.path,
                int(st.session_state.get("log_csv_auto_import_mtime_ns") or 0),
            )
        selection_state_key = f"log_csv_to_docs_selected_indices_{upload_signature_hash}"
        editor_version_key = f"log_csv_to_docs_editor_version_{upload_signature_hash}"
        failed_selection_state_key = f"log_csv_to_docs_failed_indices_{upload_signature_hash}"
        retry_failed_pending_key = f"log_csv_to_docs_retry_failed_pending_{upload_signature_hash}"
        retry_source_status_key = f"log_csv_to_docs_retry_source_status_{upload_signature_hash}"
        retry_of_run_id_key = f"log_csv_to_docs_retry_of_run_id_{upload_signature_hash}"
        retry_attempt_key = f"log_csv_to_docs_retry_attempt_{upload_signature_hash}"
        resume_pending_key = f"log_csv_to_docs_resume_pending_{upload_signature_hash}"
        selection_initialized_key = f"log_csv_to_docs_selection_initialized_{upload_signature_hash}"
        st.session_state.setdefault(selection_state_key, [])
        st.session_state.setdefault(editor_version_key, 0)
        st.session_state.setdefault(failed_selection_state_key, [])
        st.session_state.setdefault(retry_failed_pending_key, False)
        st.session_state.setdefault(retry_source_status_key, {})
        st.session_state.setdefault(retry_of_run_id_key, "")
        st.session_state.setdefault(retry_attempt_key, 0)
        st.session_state.setdefault(resume_pending_key, False)
        if not st.session_state.get(selection_initialized_key):
            st.session_state[selection_state_key] = (
                []
                if history_completed or history_processing_blocked
                else list(history_default_selectable_indices)
            )
            st.session_state[selection_initialized_key] = True
        elif history_completed or history_processing_blocked:
            st.session_state[selection_state_key] = []
        loaded_batch_state = load_current_log_csv_batch_state()
        legacy_source_batch_state = (
            loaded_batch_state
            if log_csv_legacy_batch_source_matches(loaded_batch_state, uploaded_log_csv)
            else None
        )
        persisted_batch_state = loaded_batch_state
        if not log_csv_batch_state_matches(persisted_batch_state, upload_signature_hash):
            persisted_batch_state = None
        persisted_results: list[dict[str, object]] = []
        if persisted_batch_state is not None:
            persisted_results = list(persisted_batch_state.get("results") or [])
            if persisted_results:
                st.session_state["log_csv_to_docs_save_results"] = persisted_results
                st.session_state["log_csv_to_docs_save_results_signature"] = upload_signature_hash
            st.session_state[failed_selection_state_key] = list(
                persisted_batch_state.get("failed_indices") or []
            )
            persisted_result_log_path = str(persisted_batch_state.get("result_log_path") or "")
            if persisted_result_log_path:
                st.session_state["log_csv_to_docs_result_log_path"] = persisted_result_log_path
            persisted_run_id = str(persisted_batch_state.get("run_id") or "")
            if persisted_run_id:
                st.session_state["log_csv_to_docs_result_run_id"] = persisted_run_id
        completion_confirmation_pending = log_csv_completion_is_pending(persisted_batch_state)
        history_seed_batch_state = persisted_batch_state or legacy_source_batch_state
        if history_seed_batch_state is not None:
            history_seed_results = list(history_seed_batch_state.get("results") or [])
            history_seed_selected_indices = [
                int(index) for index in history_seed_batch_state.get("selected_indices") or []
            ]
            history_seed_result_indices: set[int] = set()
            for history_seed_result in history_seed_results:
                try:
                    history_seed_result_indices.add(int(history_seed_result.get("source_index")))
                except (TypeError, ValueError):
                    continue
            legacy_batch_can_seed_history = bool(
                not history_processing_blocked
                and log_csv_history_can_seed_from_completed_batch(
                    history_entry,
                    batch_status=str(history_seed_batch_state.get("status") or ""),
                    selected_indices=history_seed_selected_indices,
                    result_indices=history_seed_result_indices,
                    matched_count=len(matched_rows),
                )
            )
            if (
                not history_processing_blocked and persisted_batch_state is not None
                and not csv_worker_is_running(LOG_CSV_WORKER_LOCK_PATH)
                and history_seed_results
                and history_seed_result_indices.issubset(set(range(len(matched_rows))))
                and set(persisted_batch_state["completed_indices"]) == history_seed_result_indices
            ):
                legacy_batch_can_seed_history = True
            if legacy_batch_can_seed_history:
                legacy_completed_at = str(
                    history_seed_batch_state.get("completed_at")
                    or history_seed_batch_state.get("updated_at")
                    or log_csv_batch_now_iso()
                )
                history_entry = prepare_log_csv_history_entry(
                    processing_history,
                    LogCsvHistoryStart(
                        csv_hash=csv_content_hash,
                        row_hashes=tuple(original_row_hashes),
                        selected_indices=tuple(
                            log_csv_history_source_indices_for_matches(
                                matched_rows,
                                history_seed_selected_indices,
                            )
                        ),
                        review_indices=tuple(history_review_source_indices),
                        started_at=legacy_completed_at,
                        new_row_count=int(history_entry.get("new_row_count") or 0),
                        source_date=history_source_date,
                    ),
                )
                for history_seed_result in history_seed_results:
                    try:
                        matched_index = int(history_seed_result.get("source_index"))
                    except (TypeError, ValueError):
                        continue
                    if matched_index < 0 or matched_index >= len(matched_rows):
                        continue
                    history_outcome = log_csv_history_outcome_from_result(history_seed_result)
                    for source_index in log_csv_history_source_indices(matched_rows[matched_index]):
                        history_entry = record_log_csv_history_outcome(
                            history_entry,
                            LogCsvHistoryUpdate(
                                source_index=source_index,
                                outcome=history_outcome,
                                updated_at=legacy_completed_at,
                            ),
                        )
                history_entry = finalize_log_csv_history_entry(
                    history_entry,
                    legacy_completed_at,
                )
                processing_history = upsert_log_csv_history_entry(
                    processing_history,
                    history_entry,
                )
                if not persist_current_log_csv_processing_history(processing_history):
                    history_processing_blocked = True
                    st.error(
                        "기존 완료 이력을 승계해 저장하지 못해 Docs 전송을 중지했습니다."
                    )
                history_completed = log_csv_history_is_completed(history_entry)
                unresolved_history_source_indices = set(
                    unresolved_log_csv_history_indices(history_entry)
                )
                history_default_selectable_indices = [
                    matched_index
                    for matched_index in selectable_indices
                    if unresolved_history_source_indices.intersection(
                        log_csv_history_source_indices(matched_rows[matched_index])
                    )
                ]
                st.session_state[selection_state_key] = (
                    []
                    if history_completed or history_processing_blocked
                    else list(history_default_selectable_indices)
                )
                if not history_processing_blocked:
                    refresh_log_csv_processing_history_summary(
                        history_summary_placeholder,
                        processing_history,
                    )
        persisted_run_recently_active = csv_worker_is_running(LOG_CSV_WORKER_LOCK_PATH)
        is_saving_docs = bool(
            st.session_state.get("log_csv_to_docs_is_saving", False)
            or persisted_run_recently_active
        )
        if log_csv_history_processing_is_finished(history_entry) and not history_processing_blocked:
            def verify_completed_csv_record(index: int) -> LogCsvStorageCheck:
                item = matched_rows[index]
                row = item["source_row"]
                matches = item.get("docs_matches") or []
                if len(matches) != 1:
                    return LogCsvStorageCheck(index, "review", "학생 문서 대상을 다시 확인해야 합니다.")
                try:
                    response = verify_summary_in_google_doc_after_timeout(
                        load_google_apps_script_webapp_url(), str(row.get("name") or ""),
                        parse_log_export_date(str(row.get("date") or "")),
                        str(item.get("docs_preview_text") or ""),
                        str(matches[0].get("doc_id") or ""), build_log_csv_dedupe_key(row),
                        subject=str((item.get("applied_subject_info") or {}).get("applied_subject") or row.get("curriculum") or ""),
                        book=google_docs_book_from_log_item(item),
                    )
                except (ValueError, WorkflowStepError):
                    return LogCsvStorageCheck(index, "review", "저장 확인 요청을 구성하지 못했습니다.")
                found = response.get("found_existing")
                return classify_log_csv_storage_check(index, LogCsvStorageResponse(
                    ok=response.get("ok") is True, verify_only=response.get("verify_only") is True,
                    found_existing=found if isinstance(found, bool) else None,
                ))

            def persist_completed_csv_recheck(checks: list[LogCsvStorageCheck]) -> bool:
                updated_at = log_csv_batch_now_iso()
                rechecked_entry = history_entry
                rechecked_state = create_log_csv_batch_state(
                    upload_signature=upload_signature_hash,
                    source_csv_name=str(getattr(uploaded_log_csv, "name", "")),
                    source_csv_size=int(getattr(uploaded_log_csv, "size", 0) or 0),
                    source_csv_path=str(uploaded_log_csv.path) if isinstance(uploaded_log_csv, LocalLogCsvUpload) else "",
                    source_mtime_ns=int(st.session_state.get("log_csv_auto_import_mtime_ns") or 0) if isinstance(uploaded_log_csv, LocalLogCsvUpload) else 0,
                    selected_indices=[check.source_index for check in checks],
                    started_at=updated_at, run_id=f"docs_recheck_{time.time_ns()}",
                )
                for check in checks:
                    item = matched_rows[check.source_index]
                    match = (item.get("docs_matches") or [{}])[0]
                    confirmed = check.outcome == "duplicate"
                    result_status = {"duplicate": "이미 저장됨", "failure": "실패", "review": "timeout_unknown"}[check.outcome]
                    result = build_log_csv_to_docs_result_entry(
                        item["source_row"], result_status,
                        check.message, source_index=check.source_index,
                        doc_id=str(match.get("doc_id") or ""), doc_url=str(match.get("doc_url") or ""),
                        duplicate_skipped=confirmed, newly_added=False,
                        needs_review=check.outcome == "review", timeout_unknown=check.outcome == "review",
                        summary_text=str(item.get("docs_preview_text") or ""),
                    )
                    rechecked_state = record_log_csv_batch_result(
                        rechecked_state, source_index=check.source_index, result=result,
                        failed=check.outcome == "failure", updated_at=updated_at,
                    )
                    for source_index in log_csv_history_source_indices(item):
                        rechecked_entry = record_log_csv_history_outcome(
                            rechecked_entry, LogCsvHistoryUpdate(source_index, check.outcome, updated_at),
                        )
                rechecked_state = complete_log_csv_batch_state(
                    rechecked_state, result_log_path="", updated_at=updated_at,
                )
                try:
                    with CsvRunLease(LOG_CSV_WORKER_LOCK_PATH):
                        latest_history = load_log_csv_processing_history(LOG_CSV_PROCESSING_HISTORY_PATH)
                        latest_entry = find_log_csv_history_entry(latest_history, history_entry["csv_hash"])
                        if latest_entry is not None and latest_entry != history_entry:
                            return False
                        save_log_csv_batch_state(LOG_CSV_DOCS_STATE_PATH, rechecked_state)
                        save_log_csv_processing_history(
                            LOG_CSV_PROCESSING_HISTORY_PATH,
                            upsert_log_csv_history_entry(latest_history, rechecked_entry),
                        )
                except (OSError, CsvRunBusyError, LogCsvProcessingHistoryError):
                    return False
                return True

            recheck_indices = tuple(
                index for index, item in enumerate(matched_rows)
                if any(history_entry["outcomes"][source_index] in {"success", "duplicate", "failure", "review"}
                       for source_index in log_csv_history_source_indices(item))
            )
            render_completed_log_csv_recheck(history_entry, LogCsvRecheckActions(
                indices=recheck_indices,
                enabled=not is_saving_docs and bool(load_google_apps_script_webapp_url()),
                verify=verify_completed_csv_record, persist=persist_completed_csv_recheck,
                rerun=rerun_log_csv_workspace,
            ))
        if (
            history_completed
            and not history_processing_blocked
            and not completion_confirmation_pending
        ):
            if not is_saving_docs:
                save_button_slot.button(
                    "선택한 기록을 학생 Docs에 추가",
                    disabled=True,
                    help="이미 처리 완료한 CSV입니다.",
                    key="log_csv_to_docs_save_button",
                )
            return
        if (
            not history_completed
            and not history_processing_blocked
            and str((persisted_batch_state or {}).get("status") or "") != "completed"
        ):
            render_log_csv_persisted_progress(upload_signature_hash)
        current_selected_indices = [
            index
            for index in st.session_state.get(selection_state_key, [])
            if index in selectable_indices
        ]
        failed_indices_for_upload = [
            index
            for index in st.session_state.get(failed_selection_state_key, [])
            if index in selectable_indices
        ]
        resume_indices_for_upload = (
            remaining_log_csv_batch_indices(persisted_batch_state, selectable_indices)
            if persisted_batch_state is not None
            else []
        )
        resume_available = bool(
            resume_indices_for_upload
            and not persisted_run_recently_active
            and str((persisted_batch_state or {}).get("status") or "") != "completed"
        )
        current_selected_indices, duplicate_auto_deselected_rows = auto_deselect_log_csv_duplicate_selection(
            matched_rows,
            current_selected_indices,
        )
        if duplicate_auto_deselected_rows:
            st.session_state[editor_version_key] += 1
        st.session_state[selection_state_key] = current_selected_indices

        content_row_pairs = [
            (index, item)
            for index, item in enumerate(matched_rows)
            if str(item.get("docs_preview_text") or "").strip()
        ]
        resolved_history_source_indices = set(range(len(original_row_hashes))) - set(
            unresolved_history_source_indices
        )
        visible_row_pairs = [
            (index, item)
            for index, item in content_row_pairs
            if not set(log_csv_history_source_indices(item)).issubset(
                resolved_history_source_indices
            )
        ]
        hidden_empty_row_count = len(matched_rows) - len(content_row_pairs)
        table_rows = []
        selected_index_set = set(st.session_state.get(selection_state_key, []))
        for index, item in visible_row_pairs:
            row = item["source_row"]
            lesson_type_info = item.get("lesson_type_info") or infer_lesson_type_from_log_row(row)
            applied_subject_info = item.get("applied_subject_info") or {}
            table_rows.append(
                {
                    "선택": index in selected_index_set and index in selectable_indices,
                    "date": row.get("date", ""),
                    "name": row.get("name", ""),
                    "sid": row.get("sid", ""),
                    "school": row.get("school", ""),
                    "curriculum": row.get("curriculum", ""),
                    "CSV 과목": applied_subject_info.get("csv_subject") or "-",
                    "최종 적용 과목": applied_subject_info.get("applied_subject") or "미확인",
                    "적용 방식": applied_subject_info.get("applied_source") or "미확인",
                    "수업 유형": lesson_type_info.get("lesson_type", "미확인"),
                    "숙제 병합": (
                        ", ".join(str(value) for value in item.get("merged_homework_items", []) if str(value).strip())
                        if item.get("merged_duplicate_homework")
                        else "-"
                    ),
                    "notes 글자 수": item["notes_length"],
                    "Google Docs 매칭 여부": item["docs_status"],
                    "doc_id 또는 doc_url 일부": short_preview_text(str(item["doc_target"])),
                    "상태": item["status"],
                }
            )

        edited_table_rows = (
            []
            if history_completed
            else st.data_editor(
                table_rows,
                use_container_width=True,
                hide_index=True,
                disabled=True
                if history_processing_blocked
                else [
                    "date",
                    "name",
                    "sid",
                    "school",
                    "curriculum",
                    "CSV 과목",
                    "최종 적용 과목",
                    "적용 방식",
                    "수업 유형",
                    "숙제 병합",
                    "notes 글자 수",
                    "Google Docs 매칭 여부",
                    "doc_id 또는 doc_url 일부",
                    "상태",
                ],
                column_config={"선택": st.column_config.CheckboxColumn("선택")},
                key=f"log_csv_to_docs_selection_table_{upload_signature_hash}_{st.session_state[editor_version_key]}",
            )
        )
        visible_indices = [index for index, _item in visible_row_pairs]
        selected_indices = (
            []
            if history_completed or history_processing_blocked
            else [
                visible_indices[row_index]
                for row_index, row in enumerate(edited_table_rows)
                if bool(row.get("선택")) and visible_indices[row_index] in selectable_indices
            ]
        )
        st.session_state[selection_state_key] = selected_indices
        selected_count = len(selected_indices)
        if selected_count >= 30:
            st.info("30명 이상 일괄 저장합니다. 진행 상황은 자동 저장되며 화면을 새로 열어도 확인할 수 있습니다.")
        if selected_count >= 50:
            st.warning("50명 이상은 처리 시간이 길 수 있습니다. 가능하면 나누어 저장하세요.")

        duplicate_diagnostic_rows = build_log_csv_duplicate_diagnostic_rows(matched_rows, selected_indices)
        if save_excluded_count and not history_completed and not history_processing_blocked:
            st.info(
                f"현재 선택된 {selected_count:,}건만 Google Docs에 저장됩니다. "
                f"저장 제외 {save_excluded_count:,}건은 전송되지 않습니다."
            )
            render_log_csv_save_excluded_list(save_excluded_items)
        if hidden_empty_row_count:
            st.caption(f"기록 내용이 없는 빈 행 {hidden_empty_row_count:,}건은 미리보기에서 숨겼습니다.")

        if show_developer_diagnostics:
            with st.expander("CSV 진단 요약", expanded=False):
                st.write(
                    "- ADMIN consumed 필터: "
                    f"`{'ON' if consumed_filter_diagnostic.get('enabled') else 'OFF'}` · "
                    f"업로드 CSV import 제외 `{int(consumed_filter_diagnostic.get('removed_count') or 0):,}`건"
                )
                if consumed_filter_diagnostic.get("failed_open"):
                    st.warning("ADMIN consumed 필터가 fail-open으로 동작했습니다. 원본 notes를 유지했습니다.")
                removed_rows = list(consumed_filter_diagnostic.get("removed_rows") or [])
                if removed_rows:
                    st.dataframe(removed_rows[:50], use_container_width=True, hide_index=True)
                admin_cols = st.columns(5)
                admin_cols[0].metric("admin 매칭 성공", f"{admin_match_counts.get('매칭 성공', 0):,}건")
                admin_cols[1].metric("admin 후보 여러 개", f"{admin_match_counts.get('후보 여러 개', 0):,}건")
                admin_cols[2].metric("admin 과목 불일치", f"{admin_match_counts.get('과목 불일치', 0):,}건")
                admin_cols[3].metric(
                    "admin 후보 없음",
                    f"{admin_match_counts.get('admin 후보 없음', 0) + admin_match_counts.get('요일 후보 없음', 0):,}건",
                )
                admin_cols[4].metric("admin 연결 실패", f"{admin_match_counts.get('admin 연결 실패', 0):,}건")
                lesson_cols = st.columns(5)
                lesson_cols[0].metric("정규강의", f"{lesson_type_counts.get('정규강의', 0):,}건")
                lesson_cols[1].metric("특강", f"{lesson_type_counts.get('특강', 0):,}건")
                lesson_cols[2].metric("미확인", f"{lesson_type_counts.get('미확인', 0):,}건")
                lesson_cols[3].metric("수업유형 확인 필요", f"{lesson_type_counts.get('확인 필요', 0):,}건")
                lesson_cols[4].metric("수업 특강/수능특강 내용", f"{len(special_class_diagnostic_rows):,}건")

        if duplicate_auto_deselected_rows:
            st.warning(
                "현재 CSV 안에 같은 학생/같은 날짜/같은 내용으로 보이는 중복 후보가 있어 1건만 전송 대상으로 남기고 나머지는 전송하지 않게 자동 선택 해제했습니다. "
                "필요하면 수동으로 다시 선택할 수 있습니다."
            )
            if show_developer_diagnostics:
                with st.expander(f"중복 후보 자동 선택 해제 ({len(duplicate_auto_deselected_rows):,}개)", expanded=True):
                    if st.checkbox(
                        "상세 표 표시",
                        key=f"log_csv_duplicate_auto_deselected_table_{upload_signature_hash}",
                    ):
                        st.dataframe(
                            duplicate_auto_deselected_rows,
                            use_container_width=True,
                            hide_index=True,
                        )

        if duplicate_diagnostic_rows:
            st.warning(
                "같은 학생/같은 날짜/같은 내용으로 보이는 행이 있습니다. "
                "둘 다 선택하면 Google Docs에 같은 날 기록이 2개 생길 수 있습니다. 하나만 선택하세요."
            )
            if show_developer_diagnostics:
                with st.expander(f"중복 후보 진단 ({len(duplicate_diagnostic_rows):,}개)", expanded=True):
                    if st.checkbox(
                        "상세 표 표시",
                        key=f"log_csv_duplicate_diagnostic_table_{upload_signature_hash}",
                    ):
                        st.dataframe(
                            duplicate_diagnostic_rows,
                            use_container_width=True,
                            hide_index=True,
                        )

        if duplicate_auto_deselected_rows and not show_developer_diagnostics:
            st.info("자동 선택 해제 상세 표는 개발자 옵션에서 확인할 수 있습니다.")

        if show_developer_diagnostics:
            with st.expander(f"수업 유형 상세 진단 ({len(lesson_type_diagnostic_rows):,}개)", expanded=False):
                if st.checkbox(
                    "상세 표 표시",
                    key=f"log_csv_lesson_type_diagnostic_table_{upload_signature_hash}",
                ):
                    st.dataframe(
                        lesson_type_diagnostic_rows,
                        use_container_width=True,
                        hide_index=True,
                    )

        if special_class_diagnostic_rows and show_developer_diagnostics:
            with st.expander(f"수업 특강/수능특강 내용 진단 ({len(special_class_diagnostic_rows):,}개)", expanded=True):
                st.info("수능특강/수특은 교재·내용 언급으로만 표시하며 수업 유형 특강으로 분류하지 않습니다.")
                if st.checkbox(
                    "상세 표 표시",
                    key=f"log_csv_special_class_diagnostic_table_{upload_signature_hash}",
                ):
                    st.dataframe(
                        special_class_diagnostic_rows,
                        use_container_width=True,
                        hide_index=True,
                    )

        if docs_progress_diagnostic_rows and show_developer_diagnostics:
            with st.expander(f"Google Docs 전송용 진도 진단 ({len(docs_progress_diagnostic_rows):,}개)", expanded=False):
                if st.checkbox(
                    "상세 표 표시",
                    key=f"log_csv_docs_progress_diagnostic_table_{upload_signature_hash}",
                ):
                    st.dataframe(
                        docs_progress_diagnostic_rows,
                        use_container_width=True,
                        hide_index=True,
                    )

        previewable_rows = [
            item
            for item in matched_rows
            if str(item.get("docs_preview_text") or "").strip()
        ]
        if previewable_rows and show_developer_diagnostics:
            preview_labels = [
                (
                    f"{item['row_index']}행 · "
                    f"{item['source_row'].get('date', '')} · "
                    f"{item['source_row'].get('name', '')} · "
                    f"{item['status']}"
                )
                for item in previewable_rows
            ]
            selected_preview_label = st.selectbox(
                "본문 미리보기",
                options=preview_labels,
                key="log_csv_to_docs_preview_row_v2",
            )
            selected_preview = previewable_rows[preview_labels.index(selected_preview_label)]
            st.text_area(
                "Google Docs에 추가될 본문 미리보기",
                str(selected_preview.get("docs_preview_text") or ""),
                height=360,
                key="log_csv_to_docs_preview_text_v2",
            )

        web_app_url = load_google_apps_script_webapp_url()
        if not web_app_url:
            st.warning("Google Apps Script Web App URL이 저장되어 있지 않아 CSV 저장을 실행할 수 없습니다.")

        if st.session_state.get("log_csv_to_docs_pacing_version") != LOG_CSV_TO_DOCS_PACING_VERSION:
            st.session_state["log_csv_to_docs_batch_size"] = LOG_CSV_TO_DOCS_DEFAULT_BATCH_SIZE
            st.session_state["log_csv_to_docs_pacing_version"] = LOG_CSV_TO_DOCS_PACING_VERSION
        st.session_state.setdefault("log_csv_to_docs_batch_size", LOG_CSV_TO_DOCS_DEFAULT_BATCH_SIZE)
        if st.session_state.get("log_csv_to_docs_batch_size") not in LOG_CSV_TO_DOCS_BATCH_SIZE_OPTIONS:
            st.session_state["log_csv_to_docs_batch_size"] = LOG_CSV_TO_DOCS_DEFAULT_BATCH_SIZE
        save_batch_size = int(st.session_state.get("log_csv_to_docs_batch_size") or LOG_CSV_TO_DOCS_DEFAULT_BATCH_SIZE)
        row_pause_seconds = LOG_CSV_TO_DOCS_ROW_PAUSE_SECONDS
        batch_pause_seconds = LOG_CSV_TO_DOCS_BATCH_PAUSE_SECONDS
        lock_retry_pause_seconds = LOG_CSV_TO_DOCS_LOCK_RETRY_PAUSE_SECONDS
        if show_developer_diagnostics:
            with st.expander("Google Docs 저장 안전 설정", expanded=False):
                save_batch_size = st.selectbox(
                    "저장 batch size",
                    list(LOG_CSV_TO_DOCS_BATCH_SIZE_OPTIONS),
                    index=list(LOG_CSV_TO_DOCS_BATCH_SIZE_OPTIONS).index(save_batch_size),
                    key="log_csv_to_docs_batch_size",
                    disabled=is_saving_docs or history_completed or history_processing_blocked,
                    help="기본값은 10명입니다. 서로 다른 학생 문서는 최대 3건씩 전송하며 같은 학생 기록은 순서를 유지합니다.",
                )

        retry_failed_pending = bool(st.session_state.get(retry_failed_pending_key, False))
        if retry_failed_pending and (
            selected_count == 0
            or not bool(web_app_url)
            or is_saving_docs
            or history_completed
            or history_processing_blocked
        ):
            st.session_state[retry_failed_pending_key] = False
            retry_failed_pending = False
        resume_pending = bool(st.session_state.get(resume_pending_key, False))
        if resume_pending and (
            not resume_indices_for_upload
            or not bool(web_app_url)
            or is_saving_docs
            or history_completed
            or history_processing_blocked
        ):
            st.session_state[resume_pending_key] = False
            resume_pending = False

        resume_button_slot = st.empty()
        if (
            resume_available
            and not is_saving_docs
            and not history_completed
            and not history_processing_blocked
        ):
            with resume_button_slot.container():
                if st.button(
                    f"남은 작업 이어서 저장 ({len(resume_indices_for_upload):,}건)",
                    disabled=not bool(web_app_url),
                    key=f"log_csv_to_docs_resume_button_{upload_signature_hash}",
                ):
                    st.session_state[selection_state_key] = list(resume_indices_for_upload)
                    st.session_state[resume_pending_key] = True
                    st.session_state[editor_version_key] += 1
                    rerun_log_csv_workspace()

        pending_auto_export_token = str(
            st.session_state.get("log_csv_auto_export_pending_token") or ""
        )
        started_auto_export_token = str(
            st.session_state.get("log_csv_auto_export_started_token") or ""
        )
        auto_export_requested = should_auto_export_log_csv(
            current_token=auto_export_token,
            pending_token=pending_auto_export_token,
            started_token=started_auto_export_token,
            selected_count=selected_count,
            has_web_app_url=bool(web_app_url),
            is_saving=(
                is_saving_docs
                or history_completed
                or history_processing_blocked
                or completion_confirmation_pending
                or str((persisted_batch_state or {}).get("run_id") or "").startswith("docs_recheck_")
            ),
        )
        if (
            auto_export_token
            and auto_export_token == pending_auto_export_token
            and auto_export_token != started_auto_export_token
            and selected_count == 0
            and not history_processing_blocked
        ):
            st.session_state["log_csv_auto_export_started_token"] = auto_export_token
            st.session_state["log_csv_auto_export_pending_token"] = ""
            if not history_completed:
                st.info("새 CSV를 불러왔지만 Google Docs에 전송할 수 있는 기록이 없습니다.")

        save_button_clicked = False
        if not is_saving_docs:
            with save_button_slot.container():
                save_button_clicked = st.button(
                    "선택한 기록을 학생 Docs에 추가",
                    type="primary",
                    disabled=(
                        selected_count == 0
                        or not bool(web_app_url)
                        or history_completed
                        or history_processing_blocked
                        or completion_confirmation_pending
                    ),
                    key="log_csv_to_docs_save_button",
                )
        if auto_export_requested:
            st.session_state["log_csv_auto_export_started_token"] = auto_export_token
            st.session_state["log_csv_auto_export_pending_token"] = ""
            st.info(f"새 CSV 감지 · 선택된 {selected_count:,}건을 자동으로 내보냅니다.")

        if save_button_clicked or retry_failed_pending or auto_export_requested or resume_pending:
            resume_button_slot.empty()
            save_button_slot.empty()
            retry_run_active = bool(retry_failed_pending)
            resume_run_active = bool(resume_pending)
            retry_source_status_by_index = (
                dict(st.session_state.get(retry_source_status_key) or {}) if retry_run_active else {}
            )
            retry_of_run_id = str(st.session_state.get(retry_of_run_id_key) or "") if retry_run_active else ""
            retry_attempt = int(st.session_state.get(retry_attempt_key) or 0)
            if retry_run_active:
                retry_attempt += 1
                st.session_state[retry_attempt_key] = retry_attempt
            st.session_state[retry_failed_pending_key] = False
            st.session_state[resume_pending_key] = False
            st.session_state["log_csv_to_docs_is_saving"] = True
            run_started_at = log_csv_batch_now_iso()
            run_timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
            source_csv_path = ""
            source_mtime_ns = 0
            if isinstance(uploaded_log_csv, LocalLogCsvUpload):
                source_csv_path = str(uploaded_log_csv.path)
                try:
                    source_mtime_ns = uploaded_log_csv.path.stat().st_mtime_ns
                except OSError:
                    source_mtime_ns = 0
            if resume_run_active and persisted_batch_state is not None:
                run_state = resume_log_csv_batch_state(
                    persisted_batch_state,
                    updated_at=run_started_at,
                )
                results = list(run_state.get("results") or [])
                process_selected_indices = remaining_log_csv_batch_indices(
                    run_state,
                    selectable_indices,
                )
                run_scope_indices = list(run_state.get("selected_indices") or [])
                completed_before = len(run_state.get("completed_indices") or [])
            else:
                run_scope_indices = list(selected_indices)
                process_selected_indices = list(run_scope_indices)
                completed_before = 0
                run_state = create_log_csv_batch_state(
                    upload_signature=upload_signature_hash,
                    source_csv_name=str(getattr(uploaded_log_csv, "name", "")),
                    source_csv_size=int(getattr(uploaded_log_csv, "size", 0) or 0),
                    source_csv_path=source_csv_path,
                    source_mtime_ns=source_mtime_ns,
                    selected_indices=run_scope_indices,
                    started_at=run_started_at,
                    run_id=f"log_csv_to_docs_{run_timestamp}",
                )
                previous_in_flight = [
                    request
                    for request in log_csv_batch_in_flight_requests(persisted_batch_state)
                    if request["source_index"] in run_scope_indices
                ]
                if previous_in_flight:
                    run_state = set_log_csv_batch_in_flight_requests(
                        run_state,
                        requests=previous_in_flight,
                        updated_at=run_started_at,
                    )
                results: list[dict[str, object]] = []
            should_sync_admin_notes = bool(
                pending_admin_sync is not None
                and pending_admin_sync_indices.intersection(process_selected_indices)
            )
            if should_sync_admin_notes and pending_admin_sync is not None:
                try:
                    admin_sync_result = sync_admin_notes(pending_admin_sync)
                    st.session_state["last_admin_notes_save_key"] = pending_admin_sync.preview_key
                    if admin_sync_result.response is not None:
                        st.session_state["last_admin_notes_save_result"] = admin_sync_result.response
                    st.session_state.pop("admin_notes_save_error", None)
                    if admin_sync_result.updated:
                        st.info("현재 정리본을 5A ADMIN 특이사항에 자동 반영했습니다.")
                    else:
                        st.caption("현재 정리본은 이미 5A ADMIN 특이사항에 반영되어 있습니다.")
                except WorkflowStepError as exc:
                    st.warning(
                        "현재 정리본의 5A ADMIN 자동 반영에 실패했습니다. "
                        "Google Docs 저장은 계속합니다."
                    )
                    if show_developer_diagnostics:
                        st.caption(str(exc)[:800])
            history_entry = prepare_log_csv_history_entry(
                processing_history,
                LogCsvHistoryStart(
                    csv_hash=csv_content_hash,
                    row_hashes=tuple(original_row_hashes),
                    selected_indices=tuple(
                        log_csv_history_source_indices_for_matches(
                            matched_rows,
                            process_selected_indices,
                        )
                    ),
                    review_indices=tuple(history_review_source_indices),
                    started_at=run_started_at,
                    new_row_count=int(history_entry.get("new_row_count") or 0),
                    source_date=history_source_date,
                ),
            )
            processing_history = upsert_log_csv_history_entry(processing_history, history_entry)
            transfer_args = deepcopy({
                "batch_pause_seconds": batch_pause_seconds,
                "completed_before": completed_before,
                "consumed_filter_enabled": admin_consumed_filter_enabled(),
                "history_entry": history_entry,
                "lock_retry_pause_seconds": lock_retry_pause_seconds,
                "matched_rows": matched_rows,
                "process_selected_indices": process_selected_indices,
                "processing_history": processing_history,
                "results": results,
                "retry_attempt": retry_attempt,
                "retry_of_run_id": retry_of_run_id,
                "retry_run_active": retry_run_active,
                "retry_source_status_by_index": retry_source_status_by_index,
                "row_pause_seconds": row_pause_seconds,
                "run_scope_indices": run_scope_indices,
                "run_state": run_state,
                "save_batch_size": save_batch_size,
                "source_csv_name": str(getattr(uploaded_log_csv, "name", "")),
                "source_csv_size": int(getattr(uploaded_log_csv, "size", 0) or 0),
                "web_app_url": web_app_url,
            })
            st.session_state["log_csv_to_docs_is_saving"] = False
            try:
                start_csv_worker(
                    LOG_CSV_WORKER_LOCK_PATH,
                    lambda: execute_log_csv_transfer(**transfer_args),
                    lambda exc: record_csv_worker_failure(exc, transfer_args["run_state"]),
                )
            except (CsvRunBusyError, OSError) as exc:
                st.error(str(exc))
                return
            rerun_log_csv_workspace()

        batch_completion_acknowledged = bool(
            persisted_batch_state is not None
            and persisted_batch_state.get("status") == "completed"
            and str(persisted_batch_state.get("acknowledged_at") or "").strip()
        )
        if batch_completion_acknowledged:
            save_results = []
        elif st.session_state.get("log_csv_to_docs_save_results_signature") == upload_signature_hash:
            save_results = st.session_state.get("log_csv_to_docs_save_results") or []
        else:
            save_results = []
        if save_results:
            result_classifications = {
                id(result): classify_google_docs_save_result(result) for result in save_results
            }

            def count_key_for(result: dict[str, object]) -> str:
                return str(result_classifications[id(result)].get("count_key") or "")

            def group_for(result: dict[str, object]) -> str:
                return str(result_classifications[id(result)].get("group") or "")

            timeout_results = [result for result in save_results if count_key_for(result) == "timeout_unknown"]
            lock_timeout_results = [
                result for result in save_results if count_key_for(result) == "lock_timeout_retryable"
            ]
            duplicate_write_suspected_results = [
                result for result in save_results if count_key_for(result) == "duplicate_write_suspected"
            ]
            failed_results = [result for result in save_results if count_key_for(result) == "failed"]
            retry_target_results = [
                result
                for result in save_results
                if bool(result_classifications[id(result)].get("retry_allowed"))
            ]

            def retry_selection_from_results(
                results: list[dict[str, object]],
            ) -> tuple[list[int], dict[str, str]]:
                retry_indices: list[int] = []
                retry_source_status: dict[str, str] = {}
                for result in results:
                    try:
                        source_index = int(result.get("source_index", ""))
                    except (TypeError, ValueError):
                        continue
                    if source_index not in selectable_indices:
                        continue
                    retry_indices.append(source_index)
                    retry_source_status[str(source_index)] = str(
                        result.get("result_status") or result.get("결과") or ""
                    )
                return retry_indices, retry_source_status

            retry_target_indices, retry_source_status_by_index_for_button = retry_selection_from_results(
                retry_target_results
            )
            review_only_results = [result for result in save_results if count_key_for(result) == "needs_review"]
            st.subheader("저장 결과")
            has_retry_issue = bool(failed_results or lock_timeout_results)
            has_review_issue = bool(timeout_results or duplicate_write_suspected_results or review_only_results)
            day_resolved_count = int(history_entry.get("resolved_count") or 0)
            day_total_count = int(history_entry.get("total_count") or 0)
            day_progress_text = f"{day_resolved_count:,}/{day_total_count:,}건"
            saved_students = current_log_csv_saved_student_keys()
            if saved_students is not None:
                saved_students.update(log_csv_confirmed_student_keys(save_results))
                completed_students = sum(day == history_source_date for day, _key in saved_students)
                day_progress_text += f" · 저장 완료 {completed_students:,}명"
            else:
                day_progress_text += " · 인원 확인 필요"
            if has_retry_issue:
                st.warning(
                    f"재시도 필요 {len(retry_target_indices):,}건 · "
                    f"오늘 전체 {day_progress_text}"
                )
            elif has_review_issue:
                st.info(f"문서 확인 필요 항목이 있습니다 · 오늘 전체 {day_progress_text}")
            elif history_completed:
                st.success(f"오늘 전체 처리 완료 · {day_progress_text}")
            else:
                st.success(f"이번 묶음 처리 완료 · 오늘 전체 {day_progress_text}")
            if completion_confirmation_pending and persisted_batch_state is not None:
                confirmation_label = (
                    "오늘 전체 완료 확인" if history_completed else "이번 묶음 확인"
                )
                if st.button(
                    confirmation_label,
                    type="primary",
                    key=(
                        "log_csv_to_docs_completion_confirmation_"
                        f"{persisted_batch_state['run_id']}"
                    ),
                ):
                    acknowledged_state = acknowledge_log_csv_batch_state(
                        persisted_batch_state,
                        acknowledged_at=log_csv_batch_now_iso(),
                    )
                    if persist_current_log_csv_batch_state(acknowledged_state):
                        rerun_log_csv_workspace()
                    else:
                        st.error("완료 확인 상태를 저장하지 못했습니다. 다시 눌러 주세요.")
            if st.button(
                "문제 학생만 다시 저장",
                disabled=(
                    not retry_target_indices
                    or is_saving_docs
                    or not bool(web_app_url)
                ),
                key=f"log_csv_to_docs_retry_problem_only_after_save_{upload_signature_hash}",
            ):
                retry_of_run_id = st.session_state.get("log_csv_to_docs_result_run_id", "")
                if not retry_of_run_id:
                    retry_of_run_id = result_run_id_from_log_path(
                        st.session_state.get("log_csv_to_docs_result_log_path")
                    )
                st.session_state[selection_state_key] = retry_target_indices
                st.session_state[retry_source_status_key] = retry_source_status_by_index_for_button
                st.session_state[retry_of_run_id_key] = retry_of_run_id
                st.session_state[retry_failed_pending_key] = True
                st.session_state[editor_version_key] += 1
                rerun_log_csv_workspace()
            if not retry_target_indices:
                st.caption("현재 다시 저장할 실패 또는 잠금 시간초과 항목이 없습니다.")
            retry_results = [
                result for result in save_results if str(result.get("retry_attempt") or "").strip()
            ]
            if retry_results:
                retry_success_results = [
                    result for result in retry_results if count_key_for(result) == "new_saved"
                ]
                retry_already_saved_results = [result for result in retry_results if count_key_for(result) == "already_saved"]
                retry_saved_confirmed_results = [
                    result for result in retry_results if count_key_for(result) == "saved_confirmed_after_timeout"
                ]
                retry_failed_results = [result for result in retry_results if count_key_for(result) == "failed"]
                retry_timeout_results = [
                    result for result in retry_results if count_key_for(result) == "timeout_unknown"
                ]
                retry_lock_timeout_results = [
                    result for result in retry_results if count_key_for(result) == "lock_timeout_retryable"
                ]
                retry_review_results = [result for result in retry_results if group_for(result) == "needs_check"]
                st.caption(
                    f"재시도 결과: 신규 저장 {len(retry_success_results):,}명 · "
                    f"저장 확인됨 {len(retry_saved_confirmed_results):,}명 · "
                    f"이미 저장됨 {len(retry_already_saved_results):,}명 · "
                    f"실패 {len(retry_failed_results):,}명 · "
                    f"잠금 시간초과 {len(retry_lock_timeout_results):,}명 · "
                    f"응답 시간 초과 {len(retry_timeout_results):,}명 · "
                    f"확인 필요 {len(retry_review_results):,}명"
                )
            compact_result_groups = group_compact_log_csv_results(save_results)
            render_compact_log_csv_result_list("확인 필요", compact_result_groups["needs_review"])
            render_compact_log_csv_result_list("실패", compact_result_groups["failed"])
            result_log_path = st.session_state.get("log_csv_to_docs_result_log_path")
            with st.expander("상세 진단 / 개발자 정보", expanded=False):
                show_result_detail_table = st.checkbox(
                    "상세 표 표시",
                    key=f"log_csv_to_docs_result_detail_table_{upload_signature_hash}",
                )
                result_display_columns = [
                    "batch_index",
                    "batch_size",
                    "batch_total",
                    "retry_of_run_id",
                    "retry_attempt",
                    "retry_source_status",
                    "date",
                    "name",
                    "sid",
                    "결과",
                    "메시지",
                    "dedupe_key",
                    "duplicate_reason",
                    "dedupe_key_stale",
                    "needs_review",
                    "review_reason",
                    "timeout_unknown",
                    "retry_recommended",
                    "review_category",
                    "updated_existing",
                    "result_label",
                    "already_saved_no_write",
                    "duplicate_skipped",
                    "duplicate_write_suspected",
                    "skipped_no_write",
                    "newly_added",
                    "manual_cleanup_recommended",
                    "admin_consumed_marked_count",
                    "admin_consumed_mark_error",
                    "added_row",
                    "copied_row_template",
                    "style_template_used",
                    "style_template_source",
                    "max_cell_count",
                    "row",
                    "col",
                    "doc_url",
                ]
                if show_result_detail_table:
                    st.dataframe(
                        [
                            {field: result.get(field, "") for field in result_display_columns}
                            for result in save_results
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
                if result_log_path:
                    st.write(f"저장 결과 로그: `{result_log_path}`")
                result_csv_buffer = io.StringIO()
                result_fieldnames = [
                    "batch_index",
                    "batch_size",
                    "batch_total",
                    "retry_of_run_id",
                    "retry_attempt",
                    "retry_source_status",
                    "date",
                    "name",
                    "sid",
                    "결과",
                    "메시지",
                    "duplicate_reason",
                    "dedupe_key_stale",
                    "result_label",
                    "already_saved_no_write",
                    "duplicate_skipped",
                    "duplicate_write_suspected",
                    "skipped_no_write",
                    "newly_added",
                    "manual_cleanup_recommended",
                    "doc_url",
                    "row",
                    "col",
                    "added_row",
                    "copied_row_template",
                    "style_template_used",
                    "style_template_source",
                    "max_cell_count",
                    "needs_review",
                    "review_reason",
                    "timeout_unknown",
                    "retry_recommended",
                    "review_category",
                    "updated_existing",
                    "admin_consumed_marked_count",
                    "admin_consumed_mark_error",
                ]
                result_writer = csv.DictWriter(result_csv_buffer, fieldnames=result_fieldnames)
                result_writer.writeheader()
                for result in save_results:
                    result_writer.writerow({field: result.get(field, "") for field in result_fieldnames})
                st.download_button(
                    "저장 결과 CSV 다운로드",
                    data=result_csv_buffer.getvalue().encode("utf-8-sig"),
                    file_name=f"log_csv_to_docs_result_{datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    key=f"log_csv_to_docs_result_download_{upload_signature_hash}",
                )
            for result in save_results:
                if result.get("결과") in {"성공", "중복으로 추가하지 않음"} and result.get("doc_url"):
                    st.link_button(
                        f"{result.get('name', '학생')} Google Docs 열기",
                        str(result["doc_url"]),
                        key=f"log_csv_to_docs_open_{abs(hash(str(result)))}",
                    )


def load_admin_api_base_url() -> str:
    ensure_admin_settings_files()
    try:
        raw_url = ADMIN_API_BASE_URL_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        raw_url = DEFAULT_ADMIN_API_BASE_URL
    clean_url = (raw_url or DEFAULT_ADMIN_API_BASE_URL).strip().lstrip("\ufeff").rstrip("/")
    return clean_url or DEFAULT_ADMIN_API_BASE_URL


def admin_sid_map_mtime() -> float:
    try:
        return ADMIN_SID_MAP_PATH.stat().st_mtime
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False)
def load_admin_sid_map_cached(file_mtime: float) -> list[dict[str, str]]:
    del file_mtime
    ensure_admin_settings_files()
    rows: list[dict[str, str]] = []
    try:
        with ADMIN_SID_MAP_PATH.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                student_name = (row.get("student_name") or "").strip()
                admin_sid = (row.get("admin_sid") or "").strip()
                if not student_name or not admin_sid:
                    continue
                rows.append({"student_name": student_name, "admin_sid": admin_sid})
    except Exception:
        return []
    return rows


def load_admin_sid_map() -> list[dict[str, str]]:
    return load_admin_sid_map_cached(admin_sid_map_mtime())


def extract_admin_students_from_payload(payload: object) -> list[dict]:
    return _extract_admin_students_from_payload(payload)


def admin_student_name_from_row(row: dict) -> str:
    return _admin_student_name_from_row(row)


def admin_student_sid_from_row(row: dict) -> str:
    return _admin_student_sid_from_row(row)


def admin_student_detail_from_row(row: dict) -> str:
    return _admin_student_detail_from_row(row)


def format_admin_sid_candidate_label(candidate: dict[str, str]) -> str:
    return _format_admin_sid_candidate_label(candidate)


def public_admin_sid_candidate_detail(candidate: dict[str, str]) -> str:
    return _public_admin_sid_candidate_detail(candidate)


ADMIN_SID_MANUAL_SELECT_PLACEHOLDER = "오늘 수업에 맞는 admin 행을 선택하세요"


def resolve_admin_sid_manual_candidate(
    candidates: list[dict[str, str]],
    selected_label: object,
) -> tuple[dict[str, str] | None, bool]:
    clean_label = str(selected_label or "").strip()
    if not clean_label or clean_label == ADMIN_SID_MANUAL_SELECT_PLACEHOLDER:
        return None, False
    for candidate in candidates:
        if format_admin_sid_candidate_label(candidate) == clean_label:
            return candidate, True
    return None, False


def find_admin_sid_candidate_by_sid(
    candidates: list[dict[str, str]],
    admin_sid: object,
) -> dict[str, str] | None:
    clean_sid = str(admin_sid or "").strip()
    if not clean_sid:
        return None
    for candidate in candidates:
        if str(candidate.get("admin_sid") or "").strip() == clean_sid:
            return candidate
    return None


def local_admin_sid_override_key(student_name: object) -> str:
    return normalize_log_csv_student_name(str(student_name or ""))


def load_local_admin_sid_overrides(path: Path | None = None) -> dict[str, dict[str, str]]:
    override_path = path or LOCAL_ADMIN_SID_OVERRIDES_PATH
    if not override_path.exists():
        return {}
    try:
        payload = json.loads(override_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    source = payload.get("overrides") if isinstance(payload, dict) else payload
    if not isinstance(source, dict):
        return {}
    overrides: dict[str, dict[str, str]] = {}
    for key, value in source.items():
        if not isinstance(value, dict):
            continue
        normalized_name = str(value.get("normalized_student_name") or key or "").strip()
        selected_sid = str(value.get("selected_admin_sid") or "").strip()
        if not normalized_name or not selected_sid:
            continue
        overrides[normalized_name] = {
            "normalized_student_name": normalized_name,
            "selected_admin_sid": selected_sid,
            "selected_label": str(value.get("selected_label") or "").strip(),
            "updated_at": str(value.get("updated_at") or "").strip(),
        }
    return overrides


def get_local_admin_sid_override(
    student_name: object,
    path: Path | None = None,
) -> dict[str, str]:
    return load_local_admin_sid_overrides(path).get(local_admin_sid_override_key(student_name), {})


def save_local_admin_sid_override(
    student_name: object,
    candidate: dict[str, str],
    selected_label: object,
    path: Path | None = None,
) -> bool:
    normalized_name = local_admin_sid_override_key(student_name)
    selected_sid = str((candidate or {}).get("admin_sid") or "").strip()
    if not normalized_name or not selected_sid:
        return False
    override_path = path or LOCAL_ADMIN_SID_OVERRIDES_PATH
    overrides = load_local_admin_sid_overrides(override_path)
    selected_label_text = str(selected_label or "").strip() or format_admin_sid_candidate_label(candidate)
    current = overrides.get(normalized_name, {})
    if (
        current.get("selected_admin_sid") == selected_sid
        and current.get("selected_label") == selected_label_text
    ):
        return False
    overrides[normalized_name] = {
        "normalized_student_name": normalized_name,
        "selected_admin_sid": selected_sid,
        "selected_label": selected_label_text,
        "updated_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
    }
    try:
        override_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = override_path.with_name(f"{override_path.name}.tmp")
        tmp_path.write_text(
            json.dumps({"overrides": overrides}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(override_path)
    except Exception:
        return False
    return True


def build_admin_sid_auto_match_context(
    export_text: object,
    counseling_date_value: date,
) -> str:
    weekday_label = LOG_CSV_WEEKDAY_LABELS[counseling_date_value.weekday()]
    return "\n".join(
        part
        for part in [
            str(export_text or "").strip(),
            f"상담요일: {weekday_label}요일",
        ]
        if part
    )


def auto_select_admin_sid_candidate(
    candidates: list[dict[str, str]],
    context_text: str,
) -> tuple[dict[str, str] | None, str]:
    return _auto_select_admin_sid_candidate(candidates, context_text)


@st.cache_data(show_spinner=False, ttl=300)
def load_admin_students_from_api_cached(base_url: str) -> list[dict[str, str]]:
    clean_base_url = (base_url or DEFAULT_ADMIN_API_BASE_URL).strip().rstrip("/")
    try:
        import requests
        response = requests.get(f"{clean_base_url}/api/students", timeout=8)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return [{"_error": str(exc)}]

    students: list[dict[str, str]] = []
    for row in extract_admin_students_from_payload(payload):
        student_name = admin_student_name_from_row(row)
        admin_sid = admin_student_sid_from_row(row)
        if student_name and admin_sid:
            student_row = {
                "student_name": student_name,
                "admin_sid": admin_sid,
                "detail": admin_student_detail_from_row(row),
                "source": "admin API",
            }
            for key in ADMIN_STUDENT_MATCH_FIELDS:
                value = row.get(key)
                if value is not None and str(value).strip():
                    student_row[key] = str(value).strip()
            students.append(
                student_row
            )
    return students


def load_admin_students_from_api(base_url: str) -> list[dict[str, str]]:
    rows = load_admin_students_from_api_cached(base_url)
    if rows and rows[0].get("_error"):
        st.session_state["last_admin_students_lookup_error"] = rows[0]["_error"]
        return []
    st.session_state.pop("last_admin_students_lookup_error", None)
    return rows


def is_admin_server_connection_error(error: object) -> bool:
    error_text = str(error or "").lower()
    if not error_text:
        return False
    markers = [
        "httpconnectionpool",
        "max retries exceeded",
        "connectionerror",
        "connecttimeout",
        "readtimeout",
        "timeout",
        "timed out",
        "failed to establish a new connection",
        "connection refused",
        "connection aborted",
        "network is unreachable",
        "no route to host",
        "name or service not known",
        "nodename nor servname",
        "winerror 10060",
        "winerror 10061",
        "winerror 10065",
        "\uc5f0\uacb0",
        "\uc811\uc18d",
        "\uc2dc\uac04 \ucd08\uacfc",
    ]
    return any(marker in error_text for marker in markers)


def find_admin_sid_candidates_for_student(
    student_name: str,
    admin_base_url: str | None = None,
) -> list[dict[str, str]]:
    clean_name = (student_name or "").strip()
    if not clean_name:
        return []
    csv_matches = [
        {
            "student_name": row["student_name"],
            "admin_sid": row["admin_sid"],
            "detail": "수동 매핑",
            "source": "CSV",
        }
        for row in load_admin_sid_map()
        if (row.get("student_name") or "").strip() == clean_name
    ]
    if csv_matches:
        return csv_matches

    api_rows = load_admin_students_from_api(admin_base_url or load_admin_api_base_url())
    api_matches = [
        row
        for row in api_rows
        if (row.get("student_name") or "").strip() == clean_name
    ]
    unique_matches: list[dict[str, str]] = []
    seen_sids: set[str] = set()
    for candidate in api_matches:
        admin_sid = candidate.get("admin_sid", "")
        if not admin_sid or admin_sid in seen_sids:
            continue
        seen_sids.add(admin_sid)
        unique_matches.append(candidate)
    return unique_matches


def save_admin_sid_mapping(student_name: str, admin_sid: str) -> None:
    clean_name = (student_name or "").strip()
    clean_sid = (admin_sid or "").strip()
    if not clean_name or not clean_sid:
        raise WorkflowStepError("admin SID 매핑", "학생명과 admin SID를 모두 입력하세요.")

    ensure_admin_settings_files()
    existing_rows = load_admin_sid_map()
    if any(
        row.get("student_name") == clean_name and row.get("admin_sid") == clean_sid
        for row in existing_rows
    ):
        return

    needs_newline = True
    try:
        current_text = ADMIN_SID_MAP_PATH.read_text(encoding="utf-8-sig")
        needs_newline = bool(current_text and not current_text.endswith(("\n", "\r")))
    except OSError:
        current_text = ""

    with ADMIN_SID_MAP_PATH.open("a", encoding="utf-8", newline="") as file:
        if not current_text:
            file.write("student_name,admin_sid\n")
        elif needs_newline:
            file.write("\n")
        writer = csv.writer(file)
        writer.writerow([clean_name, clean_sid])

    load_admin_sid_map_cached.clear()


def render_admin_sid_mapping_editor(student_name: str, disabled: bool = False) -> None:
    clean_name = (student_name or "").strip()
    if not clean_name:
        return
    input_key = f"admin_sid_input_{abs(hash(clean_name))}"
    sid_value = st.text_input(
        "admin SID",
        placeholder="예: 양연준902",
        key=input_key,
        disabled=disabled,
    )
    if st.button("admin SID 매핑 저장", disabled=disabled or not sid_value.strip()):
        try:
            save_admin_sid_mapping(clean_name, sid_value)
            st.success("admin SID 매핑을 저장했습니다.")
            st.rerun()
        except WorkflowStepError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"admin SID 매핑 저장에 실패했습니다: {exc}")


def import_requests_for_admin():
    try:
        import requests
    except Exception as exc:
        raise WorkflowStepError(
            "admin 특이사항 저장",
            "requests 패키지를 불러올 수 없습니다. pip install -r requirements.txt를 실행하세요.",
        ) from exc
    return requests


def get_admin_log_entry(base_url: str, counseling_date_value: date, sid: str) -> dict[str, object]:
    requests = import_requests_for_admin()
    clean_base_url = (base_url or DEFAULT_ADMIN_API_BASE_URL).strip().rstrip("/")
    url = f"{clean_base_url}/api/logs/entry"
    params = {"date": counseling_date_value.isoformat(), "sid": sid}
    try:
        response = requests.get(url, params=params, timeout=10)
    except Exception as exc:
        raise WorkflowStepError("admin 특이사항 저장", f"admin GET 요청 실패: {exc}") from exc

    response_text = response.text or ""
    response_preview = response_text[:800]
    if response.status_code >= 400:
        raise WorkflowStepError(
            "admin 특이사항 저장",
            f"admin GET 오류 · HTTP {response.status_code} · {response_preview}",
        )

    try:
        data = response.json()
    except Exception as exc:
        raise WorkflowStepError(
            "admin 특이사항 저장",
            f"admin GET 응답 JSON 파싱 실패 · HTTP {response.status_code} · {response_preview}",
        ) from exc

    if isinstance(data, dict) and data.get("ok") is False:
        raise WorkflowStepError(
            "admin 특이사항 저장",
            f"admin GET 응답 오류 · HTTP {response.status_code} · {response_preview}",
        )

    entry = data.get("entry") if isinstance(data, dict) else {}
    if not isinstance(entry, dict):
        entry = {}
    notes = entry.get("notes", "")
    if notes is None:
        notes = ""

    return {
        "notes": str(notes),
        "status_code": response.status_code,
        "response_preview": response_preview,
        "raw": data,
    }


def backup_admin_notes(
    student_name: str,
    admin_sid: str,
    counseling_date_value: date,
    admin_api_base_url: str,
    existing_notes: str,
) -> Path:
    ADMIN_NOTES_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    safe_name = sanitize_filename_part(student_name)
    safe_sid = sanitize_filename_part(admin_sid)
    safe_date = safe_date_for_filename(counseling_date_value)
    backup_path = (
        ADMIN_NOTES_BACKUP_DIR
        / f"{safe_name}_{safe_date}_{safe_sid}_before_admin_notes_{timestamp}.txt"
    )
    content = "\n".join(
        [
            f"student_name: {student_name}",
            f"admin_sid: {admin_sid}",
            f"counseling_date: {counseling_date_value.isoformat()}",
            f"admin_api_base_url: {admin_api_base_url}",
            f"backed_up_at: {datetime.now(ZoneInfo('Asia/Seoul')).isoformat()}",
            "",
            "[기존 notes]",
            existing_notes or "",
        ]
    )
    backup_path.write_text(content, encoding="utf-8")
    return backup_path


def patch_admin_log_notes(
    base_url: str,
    counseling_date_value: date,
    sid: str,
    new_notes: str,
    base_notes: str,
) -> dict[str, object]:
    requests = import_requests_for_admin()
    clean_base_url = (base_url or DEFAULT_ADMIN_API_BASE_URL).strip().rstrip("/")
    url = f"{clean_base_url}/api/logs/patch"
    payload = {
        "date": counseling_date_value.isoformat(),
        "sid": sid,
        "entry": {
            "notes": new_notes,
            "updatedAt": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        },
        "base": {"notes": base_notes},
    }
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
    except Exception as exc:
        raise WorkflowStepError("admin 특이사항 저장", f"admin POST 요청 실패: {exc}") from exc

    response_text = response.text or ""
    response_preview = response_text[:800]
    try:
        data = response.json()
    except Exception as exc:
        raise WorkflowStepError(
            "admin 특이사항 저장",
            f"admin POST 응답 JSON 파싱 실패 · HTTP {response.status_code} · {response_preview}",
        ) from exc

    if response.status_code >= 400 or not isinstance(data, dict) or data.get("ok") is not True:
        error = data.get("error") if isinstance(data, dict) else None
        raise WorkflowStepError(
            "admin 특이사항 저장",
            f"admin POST 오류 · HTTP {response.status_code} · {error or response_preview}",
        )

    result = dict(data)
    result["status_code"] = response.status_code
    result["response_preview"] = response_preview
    return result


def resolved_admin_notes_for_sync(
    request: AdminNotesSyncRequest,
    existing_notes: str,
) -> str:
    if request.export_source_label == "오늘 확인할 것" and request.today_focus_text:
        return merge_today_focus_block(existing_notes, request.today_focus_text)
    return request.export_text


def effective_log_csv_content_hash(
    csv_content_hash: str,
    request: AdminNotesSyncRequest | None,
    matched_count: int,
) -> str:
    if request is None or matched_count <= 0:
        return csv_content_hash
    pending_signature = f"{csv_content_hash}|admin-notes:{request.preview_key}"
    return hashlib.sha256(pending_signature.encode("utf-8")).hexdigest()


def apply_pending_admin_notes_to_log_rows(
    log_rows: list[dict[str, str]],
    request: AdminNotesSyncRequest | None,
) -> int:
    if request is None:
        return 0

    matched_count = 0
    expected_name = normalize_log_csv_student_name(request.student_name)
    for row in log_rows:
        if normalize_log_csv_student_name(row.get("name")) != expected_name:
            continue
        try:
            row_date = parse_log_export_date(str(row.get("date") or ""))
        except WorkflowStepError:
            continue
        if row_date != request.counseling_date:
            continue
        row_sid = str(row.get("admin_sid") or row.get("sid") or "").strip()
        if row_sid and row_sid != request.admin_sid:
            continue

        row["notes"] = resolved_admin_notes_for_sync(
            request,
            str(row.get("notes") or ""),
        )
        row["_pending_admin_notes_sync_key"] = request.preview_key
        matched_count += 1
    return matched_count


def sync_admin_notes(request: AdminNotesSyncRequest) -> AdminNotesSyncResult:
    latest_entry = get_admin_log_entry(
        request.admin_base_url,
        request.counseling_date,
        request.admin_sid,
    )
    base_notes = str(latest_entry.get("notes") or "")
    new_notes = resolved_admin_notes_for_sync(request, base_notes)
    if new_notes == base_notes:
        return AdminNotesSyncResult(
            updated=False,
            notes=new_notes,
            backup_path=None,
            response=None,
        )

    backup_path = backup_admin_notes(
        request.student_name,
        request.admin_sid,
        request.counseling_date,
        request.admin_base_url,
        base_notes,
    )
    response = patch_admin_log_notes(
        request.admin_base_url,
        request.counseling_date,
        request.admin_sid,
        new_notes,
        base_notes,
    )
    return AdminNotesSyncResult(
        updated=True,
        notes=new_notes,
        backup_path=backup_path,
        response=response,
    )


def admin_notes_preview_key(
    student_name: str,
    counseling_date_value: date,
    admin_sid: str,
    admin_notes_text: str,
) -> str:
    return _admin_notes_preview_key(student_name, counseling_date_value, admin_sid, admin_notes_text)


def admin_notes_export_text_from_summary(
    today_focus_text: str | None = "",
    summary_text: str | None = "",
) -> tuple[str, str]:
    focus_block = build_admin_today_focus_block(today_focus_text or "").strip()
    if focus_block and _summary_has_required_today_focus(focus_block):
        notes_items = summary_notes_items(summary_text)
        if notes_items:
            notes_block = "*상담 참고사항*\n" + "\n".join(
                f"- {item}" for item in notes_items
            )
            return f"{focus_block}\n\n{notes_block}".strip(), "오늘 확인할 것+상담 참고사항"
        return focus_block, "오늘 확인할 것"

    cleaned_summary = remove_runtime_noise_from_summary(summary_text or "").strip()
    cleaned_summary = _format_summary_section_spacing(cleaned_summary).strip()
    if not _summary_has_meaningful_body_content(cleaned_summary):
        return "", ""
    return cleaned_summary, "정리본"

def admin_notes_export_block_message(
    *,
    student_name: object = "",
    export_text: object = "",
    export_allowed: bool = True,
    admin_sid: object = "",
) -> str:
    missing_reasons: list[str] = []
    clean_student_name = str(student_name or "").strip()
    clean_export_text = str(export_text or "").strip()
    if not clean_student_name:
        missing_reasons.append("학생이 선택되지 않았습니다")
    if not export_allowed or not clean_export_text:
        missing_reasons.append("내보낼 내용이 없습니다")
    if clean_student_name and export_allowed and clean_export_text and not str(admin_sid or "").strip():
        missing_reasons.append("저장 대상을 먼저 선택하세요")
    if not missing_reasons:
        return ""
    return "5A ADMIN 특이사항으로 내보내지 않았습니다. " + " / ".join(missing_reasons)


@st.fragment
def render_admin_notes_save_ui(
    student_name: str,
    counseling_date_value: date,
    today_focus_text: str = "",
    summary_text: str = "",
    export_allowed: bool = True,
) -> AdminNotesSyncRequest | None:
    ui_locked = st.session_state.get("is_processing", False)
    clean_student_name = (student_name or "").strip()
    clean_focus_text = (today_focus_text or "").strip()
    clean_export_text, export_source_label = admin_notes_export_text_from_summary(
        clean_focus_text,
        summary_text,
    )
    admin_base_url = load_admin_api_base_url()
    admin_export_ready = bool(export_allowed and clean_export_text)
    student_ready = bool(clean_student_name)

    sid_candidates: list[dict[str, str]] = []
    selected_candidate: dict[str, str] | None = None
    target_resolution = ""
    if admin_export_ready and student_ready:
        sid_candidates = find_admin_sid_candidates_for_student(clean_student_name, admin_base_url)

    if admin_export_ready and student_ready and len(sid_candidates) == 0:
        lookup_error = st.session_state.get("last_admin_students_lookup_error")
        if is_admin_server_connection_error(lookup_error):
            st.warning(
                "5A admin \uc11c\ubc84\uc5d0 \uc5f0\uacb0\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.\n\n"
                "\ud559\uc6d0 \ub0b4\ubd80\ub9dd\uc5d0\uc11c\ub9cc admin \ud2b9\uc774\uc0ac\ud56d \uc800\uc7a5\uc774 \uac00\ub2a5\ud569\ub2c8\ub2e4.\n"
                "\uc9d1/\uc678\ubd80\ub9dd\uc5d0\uc11c\ub294 \uc815\ub9ac\ubcf8 \uc0dd\uc131\uacfc Google Docs \ucd94\uac00\ub9cc \uc0ac\uc6a9\ud558\uc138\uc694."
            )
            with st.expander("admin \uc11c\ubc84 \uc5f0\uacb0 \uc0c1\uc138", expanded=False):
                st.caption(str(lookup_error))
        else:
            st.warning("5A admin \ud559\uc0dd \ubaa9\ub85d\uc5d0\uc11c \ud604\uc7ac \ud559\uc0dd\uba85\uc744 \ucc3e\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.")
            if lookup_error:
                with st.expander("admin \ud559\uc0dd \ubaa9\ub85d \uc870\ud68c \uc0c1\uc138", expanded=False):
                    st.caption(str(lookup_error))
            render_admin_sid_mapping_editor(clean_student_name, disabled=ui_locked)
    elif len(sid_candidates) > 1:
        candidate_labels = [
            format_admin_sid_candidate_label(candidate)
            for candidate in sid_candidates
        ]
        select_options = [ADMIN_SID_MANUAL_SELECT_PLACEHOLDER, *candidate_labels]
        select_key = f"selected_admin_sid_for_student_{abs(hash(clean_student_name))}"
        auto_default_label_key = f"{select_key}_auto_default_label"
        change_target_key = f"{select_key}_change_target"
        stored_label_exists = select_key in st.session_state
        stored_label = st.session_state.get(select_key, "")
        if stored_label and stored_label not in select_options:
            del st.session_state[select_key]
            stored_label = ""
            stored_label_exists = False
        stored_auto_default_label = st.session_state.get(auto_default_label_key, "")
        session_sid_candidate = find_admin_sid_candidate_by_sid(
            sid_candidates,
            st.session_state.get("selected_admin_sid_for_student", ""),
        )
        session_sid_source = str(st.session_state.get("selected_admin_sid_for_student_source") or "")
        if session_sid_candidate and not session_sid_source.startswith("manual"):
            session_sid_candidate = None
        override_candidate: dict[str, str] | None = None
        override = get_local_admin_sid_override(clean_student_name)
        if override:
            override_candidate = find_admin_sid_candidate_by_sid(
                sid_candidates,
                override.get("selected_admin_sid", ""),
            )
        auto_candidate, auto_match_reason = auto_select_admin_sid_candidate(
            sid_candidates,
            build_admin_sid_auto_match_context(clean_export_text, counseling_date_value),
        )
        current_auto_label = format_admin_sid_candidate_label(auto_candidate) if auto_candidate else ""
        stored_label_is_auto_default = bool(stored_label and stored_label == stored_auto_default_label)
        if stored_label_is_auto_default and stored_label != current_auto_label:
            del st.session_state[select_key]
            stored_label = ""
            stored_label_exists = False
            stored_label_is_auto_default = False
        stored_label_candidate, stored_label_selected = (
            (None, False)
            if stored_label_is_auto_default
            else resolve_admin_sid_manual_candidate(sid_candidates, stored_label)
        )
        default_candidate = stored_label_candidate or session_sid_candidate or override_candidate or auto_candidate
        if stored_label_candidate:
            default_resolution = "manual_session_label"
        elif session_sid_candidate:
            default_resolution = "manual_session_sid"
        elif override_candidate:
            default_resolution = "local_override"
        elif auto_candidate:
            default_resolution = "auto_roster_match"
        else:
            default_resolution = ""
        force_target_change = bool(st.session_state.get(change_target_key))
        if force_target_change:
            default_candidate = None
            default_resolution = ""
        default_label = (
            format_admin_sid_candidate_label(default_candidate)
            if default_candidate
            else ADMIN_SID_MANUAL_SELECT_PLACEHOLDER
        )
        if default_resolution == "auto_roster_match":
            st.session_state[auto_default_label_key] = default_label
        else:
            st.session_state.pop(auto_default_label_key, None)
        if default_candidate and not force_target_change:
            if st.button("저장 위치 바꾸기", key=f"{select_key}_change_button", disabled=ui_locked):
                st.session_state[change_target_key] = True
                st.session_state[select_key] = ADMIN_SID_MANUAL_SELECT_PLACEHOLDER
                st.session_state.pop("selected_admin_sid_for_student", None)
                st.session_state.pop("selected_admin_sid_for_student_source", None)
                st.rerun()
        selected_label = st.selectbox(
            "저장 대상 admin SID",
            options=select_options,
            index=select_options.index(default_label),
            key=select_key,
            disabled=ui_locked,
        )
        label_candidate, label_candidate_selected = resolve_admin_sid_manual_candidate(
            sid_candidates,
            selected_label,
        )
        if label_candidate_selected:
            selected_candidate = label_candidate
            selected_sid = str(selected_candidate.get("admin_sid") or "").strip()
            if default_candidate and selected_sid == str(default_candidate.get("admin_sid") or "").strip():
                target_resolution = default_resolution
            elif stored_label_exists and stored_label_selected:
                target_resolution = "manual_session_label"
            else:
                target_resolution = "manual_dropdown"
            if target_resolution.startswith("manual"):
                save_local_admin_sid_override(clean_student_name, selected_candidate, selected_label)
            st.session_state.pop(change_target_key, None)
    elif len(sid_candidates) == 1:
        selected_candidate = sid_candidates[0]
        target_resolution = "single_candidate"

    admin_sid = (selected_candidate or {}).get("admin_sid", "")
    already_saved = False
    preview_key = ""
    pending_admin_sync: AdminNotesSyncRequest | None = None
    if selected_candidate:
        st.session_state["selected_admin_sid_for_student"] = admin_sid
        st.session_state["selected_admin_sid_for_student_source"] = target_resolution
        preview_key = admin_notes_preview_key(
            clean_student_name,
            counseling_date_value,
            admin_sid,
            clean_export_text,
        )
        already_saved_key = st.session_state.get("last_admin_notes_save_key")
        already_saved = already_saved_key == preview_key
        if admin_export_ready:
            pending_admin_sync = AdminNotesSyncRequest(
                student_name=clean_student_name,
                counseling_date=counseling_date_value,
                admin_sid=admin_sid,
                admin_base_url=admin_base_url,
                export_text=clean_export_text,
                export_source_label=export_source_label,
                today_focus_text=clean_focus_text,
                preview_key=preview_key,
            )

    multiple_candidates = len(sid_candidates) > 1
    if selected_candidate:
        selected_candidate_label = format_admin_sid_candidate_label(selected_candidate)
        selected_candidate_detail = public_admin_sid_candidate_detail(selected_candidate)
        public_target_parts = [part for part in [admin_sid, selected_candidate_detail] if part]
        public_target_label = " · ".join(public_target_parts) or selected_candidate_label
        st.info(f"저장 대상: {public_target_label}")
        if multiple_candidates and target_resolution == "auto_roster_match":
            st.info(f"후보 명단 대조로 자동 선택했습니다. ({auto_match_reason})")
    elif multiple_candidates:
        st.info("동명이인/복수 후보가 있습니다. 저장 대상을 직접 선택하세요.")
    if st.session_state.get("show_developer_diagnostics", False):
        with st.expander("저장 대상 상세 진단", expanded=False):
            target_lines = [
                f"- 학생명: {clean_student_name}",
                f"- admin_sid: {admin_sid}",
                f"- 상담일: {counseling_date_value.isoformat()}",
                f"- admin API: {admin_base_url}",
                f"- 저장 위치: 5A admin 특이사항",
                f"- 후보 여러 개 여부: {'예' if multiple_candidates else '아니오'}",
            ]
            if selected_candidate:
                target_lines.append(f"- 선택한 행: {format_admin_sid_candidate_label(selected_candidate)}")
            target_lines.append(f"- 대상 확정 방식: {target_resolution or '미확정'}")
            st.write("\n".join(target_lines))
    save_clicked = False if already_saved else st.button("5A ADMIN 특이사항으로 내보내기", disabled=ui_locked)
    admin_save_result = st.empty()
    if already_saved:
        admin_save_result.caption("이미 이 정리본을 5A ADMIN 특이사항에 저장했습니다.")
    if save_clicked:
        block_message = admin_notes_export_block_message(
            student_name=clean_student_name,
            export_text=clean_export_text,
            export_allowed=export_allowed,
            admin_sid=admin_sid,
        )
        if block_message:
            admin_save_result.warning(block_message)
            return pending_admin_sync
        backup_path = None
        try:
            if pending_admin_sync is None:
                admin_save_result.warning("저장할 admin 대상과 정리본을 먼저 확인하세요.")
                return None
            sync_result = sync_admin_notes(pending_admin_sync)
            backup_path = sync_result.backup_path
            if not sync_result.updated:
                st.session_state["last_admin_notes_save_key"] = preview_key
                admin_save_result.warning("이미 같은 정리본이 5A ADMIN 특이사항에 있어 추가 저장하지 않았습니다.")
                return pending_admin_sync
            st.session_state["admin_notes_save_success"] = "정리본을 5A ADMIN 특이사항에 저장했습니다."
            st.session_state["last_admin_notes_save_key"] = preview_key
            st.session_state["last_admin_notes_save_result"] = sync_result.response
            st.session_state.pop("admin_notes_save_error", None)
            admin_save_result.success("정리본을 5A ADMIN 특이사항에 저장했습니다.")
        except WorkflowStepError as exc:
            admin_save_result.error("admin 특이사항 저장에 실패했습니다.")
            if st.session_state.get("show_developer_diagnostics", False):
                with st.expander("admin 저장 실패 상세 진단", expanded=False):
                    st.write(f"- 오류: `{str(exc)[:800]}`")
                    st.write(f"- 학생명: `{clean_student_name}`")
                    st.write(f"- admin_sid: `{admin_sid}`")
                    st.write(f"- 상담일: `{counseling_date_value.isoformat()}`")
                    st.write(f"- admin API: `{admin_base_url}`")
                    st.write(f"- 백업 파일 경로: `{backup_path or '-'}`")
    if st.session_state.get("admin_notes_save_error"):
        st.error(st.session_state.pop("admin_notes_save_error"))
    return pending_admin_sync


def format_glossary_for_prompt(glossary_terms: list[dict[str, str | list[str]]]) -> str:
    lines = []
    for item in glossary_terms[:GLOSSARY_PROMPT_LIMIT]:
        aliases = item.get("aliases") or []
        alias_text = ", ".join(aliases) if isinstance(aliases, list) and aliases else "-"
        category = item.get("category") or "-"
        note = item.get("note") or "-"
        lines.append(
            f"- {item.get('term')}: aliases={alias_text} / category={category} / note={note}"
        )
    return "\n".join(lines)


def build_whisper_initial_prompt(student_name: str, use_prompt_hint: bool) -> str | None:
    if not use_prompt_hint:
        return None
    clean_name = student_name.strip()
    if clean_name:
        return f"한국어 학원 상담 녹음입니다. 학생명은 {clean_name}입니다."
    return WHISPER_INITIAL_PROMPT
SUMMARY_STYLES = {
    "기본 상담 정리": "상담 내용을 균형 있게 정리한다.",
    "내부 기록용": "강사와 학원 내부자가 후속 조치를 확인하기 좋게 객관적으로 정리한다.",
    "다음 상담 체크용": "다음 상담에서 확인할 질문과 체크 포인트 중심으로 정리한다.",
}
PROGRESS_STAGES = [
    "녹음 파일 확인",
    "저장 파일 준비",
    "모델 로딩",
    "원문 추출",
    "원문 저장",
    "정리본 생성",
    "클립보드 복사",
]


def get_today_kst() -> date:
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def get_default_author_name() -> str:
    return os.environ.get("USERNAME") or "unknown"


def sanitize_filename_part(value: str) -> str:
    return _sanitize_filename_part(value)


def transcript_date_dir(counseling_date: date) -> Path:
    return TRANSCRIPT_DIR / counseling_date.isoformat()


def safe_date_for_filename(counseling_date: date) -> str:
    return _safe_date_for_filename(counseling_date)


def transcript_candidate_path(
    counseling_date: date,
    student_name: str,
    author_name: str,
    time_text: str,
    index: int | None = None,
) -> Path:
    safe_name = sanitize_filename_part(student_name)
    safe_date = safe_date_for_filename(counseling_date)
    safe_author = sanitize_filename_part(author_name)
    suffix = f"_{index}" if index else ""
    filename = f"{safe_name}_{safe_date}_{time_text}_{safe_author}{suffix}.txt"
    return transcript_date_dir(counseling_date) / filename


def write_transcript_file_unique(
    counseling_date: date,
    student_name: str,
    author_name: str,
    content: str,
) -> Path:
    time_text = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%H%M%S")
    date_dir = transcript_date_dir(counseling_date)
    date_dir.mkdir(parents=True, exist_ok=True)

    for index in [None, *range(2, 1000)]:
        path = transcript_candidate_path(counseling_date, student_name, author_name, time_text, index)
        try:
            with path.open("x", encoding="utf-8") as file:
                file.write(content)
            return path
        except FileExistsError:
            continue

    raise FileExistsError("사용 가능한 원문 파일명을 만들 수 없습니다.")


@st.cache_resource(show_spinner=False)
def load_whisper_model(model_size: str, compute_type: str) -> WhisperModel:
    if compute_type == "hdh-local":
        from hdhcounsel.config import Settings

        return Settings.load()
    if compute_type == "openvino-int8":
        from counsel_modules.openvino_whisper import OpenVINOWhisperModel

        return OpenVINOWhisperModel(BASE_DIR / "models" / "whisper-large-v3-turbo-int8-ov")
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device="cpu", compute_type=compute_type)


def format_timestamp(seconds: float) -> str:
    return _format_timestamp(seconds)


def format_elapsed(seconds: float) -> str:
    return _format_elapsed(seconds)


def audio_needs_whisper_wav_conversion(audio_path: Path) -> bool:
    return audio_path.suffix.lower() in {".m4a", ".mp3"}


def make_whisper_wav_path() -> Path:
    WHISPER_AUDIO_TMP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    millis = int(time.time() * 1000) % 1000
    return WHISPER_AUDIO_TMP_DIR / f"whisper_input_{timestamp}_{millis:03d}_{os.getpid()}.wav"


def convert_audio_to_whisper_wav(audio_path: Path) -> tuple[Path, list[str], str, str]:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg를 찾을 수 없어 m4a/mp3 파일을 wav로 변환할 수 없습니다.")

    output_path = make_whisper_wav_path()
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(audio_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        shell=False,
        cwd=BASE_DIR,
        timeout=600,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "ffmpeg wav 변환에 실패했습니다.\n"
            f"returncode: {completed.returncode}\n"
            f"stdout: {completed.stdout.strip() or '-'}\n"
            f"stderr: {completed.stderr.strip() or '-'}"
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg 변환 결과 wav 파일이 비어 있습니다: {output_path}")
    return output_path, command, completed.stdout or "", completed.stderr or ""


def write_extract_error_log(
    audio_path: Path,
    converted_wav_path: Path | None,
    whisper_options: dict,
    use_vad_filter: bool,
    processing_mode: str,
    exception: Exception,
    attempt_errors: list[str],
    conversion_command: list[str] | None = None,
) -> Path | None:
    EXTRACT_ERROR_LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = EXTRACT_ERROR_LOG_DIR / f"extract_error_{timestamp}_{os.getpid()}.txt"
    try:
        original_size = audio_path.stat().st_size if audio_path.exists() else "-"
    except OSError:
        original_size = "-"
    traceback_text = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
    content = "\n".join(
        [
            "원문 추출 실패 디버그 로그",
            "",
            f"원본 오디오 경로: {audio_path}",
            f"변환 wav 경로: {converted_wav_path or '-'}",
            f"원본 파일 크기: {original_size}",
            f"processing_mode: {processing_mode or '-'}",
            f"VAD 사용 여부: {use_vad_filter}",
            f"whisper 옵션: {json.dumps(whisper_options, ensure_ascii=False, default=str)}",
            f"ffmpeg 명령: {format_command(conversion_command or []) if conversion_command else '-'}",
            f"재시도 기록: {' | '.join(attempt_errors) if attempt_errors else '-'}",
            f"예외 type: {type(exception).__name__}",
            f"예외 message: {exception}",
            "",
            "traceback:",
            traceback_text,
        ]
    )
    try:
        log_path.write_text(content, encoding="utf-8")
        return log_path
    except OSError:
        return None


def consume_whisper_segments(
    model: WhisperModel,
    whisper_audio_path: Path,
    transcribe_options: dict,
    progress_callback=None,
) -> tuple[str, list[dict[str, str]]]:
    extraction_started_at = time.monotonic()
    segments, info = model.transcribe(str(whisper_audio_path), **transcribe_options)
    audio_duration = getattr(info, "duration", None)

    lines = []
    segment_rows = []
    last_progress_emit_at = 0.0
    transcript_char_count = 0
    for segment in segments:
        text = segment.text.strip()
        if text:
            lines.append(text)
            transcript_char_count += len(text) + (1 if len(lines) > 1 else 0)
            segment_rows.append(
                {
                    "start": format_timestamp(segment.start),
                    "end": format_timestamp(segment.end),
                    "text": text,
                }
            )
            if progress_callback:
                now = time.monotonic()
                progress = None
                if audio_duration and audio_duration > 0:
                    progress = min(max(segment.end / audio_duration, 0.0), 1.0)
                should_emit = (
                    last_progress_emit_at == 0.0
                    or now - last_progress_emit_at >= 1.0
                    or progress == 1.0
                )
                if not should_emit:
                    continue
                last_progress_emit_at = now
                progress_callback(
                    {
                        "elapsed": now - extraction_started_at,
                        "audio_duration": audio_duration,
                        "progress": progress,
                        "segment_count": len(segment_rows or []),
                        "char_count": transcript_char_count,
                    }
                )

    if not segment_rows:
        raise RuntimeError("음성이 인식되지 않았습니다. 녹음 볼륨, 파일 형식, VAD 설정을 확인하세요.")

    transcript = "\n".join(lines).strip()
    if not transcript:
        raise RuntimeError("음성에서 인식된 한국어 텍스트가 없습니다.")

    return transcript, segment_rows or []


def transcribe_audio_with_wav_preprocess(
    audio_path: Path,
    model: WhisperModel,
    transcribe_options: dict,
    use_vad_filter: bool,
    processing_mode: str = "",
    progress_callback=None,
) -> tuple[str, list[dict[str, str]]]:
    whisper_audio_path = audio_path
    converted_wav_path = None
    conversion_command: list[str] | None = None
    retry_used = False
    attempt_errors: list[str] = []
    st.session_state["last_extract_debug_info"] = {}

    try:
        if audio_needs_whisper_wav_conversion(audio_path):
            converted_wav_path, conversion_command, _, _ = convert_audio_to_whisper_wav(audio_path)
            whisper_audio_path = converted_wav_path

        try:
            transcript, segment_rows = consume_whisper_segments(
                model,
                whisper_audio_path,
                transcribe_options,
                progress_callback,
            )
        except Exception as first_exc:
            attempt_errors.append(f"1차 실패: {type(first_exc).__name__}: {first_exc}")
            if not use_vad_filter:
                raise
            retry_used = True
            retry_options = dict(transcribe_options)
            retry_options["vad_filter"] = False
            retry_options.pop("vad_parameters", None)
            transcript, segment_rows = consume_whisper_segments(
                model,
                whisper_audio_path,
                retry_options,
                progress_callback,
            )
            transcribe_options = retry_options

        st.session_state["last_extract_debug_info"] = {
            "converted_wav_path": str(converted_wav_path or "-"),
            "whisper_audio_path": str(whisper_audio_path),
            "retry_used": retry_used,
            "debug_log_path": "-",
        }
        return transcript, segment_rows
    except Exception as exc:
        debug_log_path = write_extract_error_log(
            audio_path=audio_path,
            converted_wav_path=converted_wav_path,
            whisper_options=transcribe_options,
            use_vad_filter=use_vad_filter,
            processing_mode=processing_mode,
            exception=exc,
            attempt_errors=attempt_errors,
            conversion_command=conversion_command,
        )
        st.session_state["last_extract_debug_info"] = {
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "converted_wav_path": str(converted_wav_path or "-"),
            "whisper_audio_path": str(whisper_audio_path),
            "retry_used": retry_used,
            "debug_log_path": str(debug_log_path or "-"),
        }
        raise
    finally:
        if converted_wav_path:
            try:
                converted_wav_path.unlink(missing_ok=True)
            except OSError:
                pass


def transcribe_audio(
    audio_path: Path,
    model: WhisperModel,
    beam_size: int,
    use_vad_filter: bool,
    student_name: str = "",
    use_prompt_hint: bool = False,
    processing_mode: str = "",
    progress_callback=None,
) -> tuple[str, list[dict[str, str]]]:
    from hdhcounsel.config import Settings

    if isinstance(model, Settings):
        from hdhcounsel.service import transcribe

        result = transcribe(audio_path, model)
        if not result.segments:
            raise RuntimeError("음성이 인식되지 않았습니다. 원본 녹음을 확인하세요.")
        rows = [
            {
                "start": format_timestamp(segment.start),
                "end": format_timestamp(segment.end),
                "text": segment.text,
                "speaker": segment.speaker or "UNKNOWN",
            }
            for segment in result.segments
        ]
        return result.text(), rows

    initial_prompt = build_whisper_initial_prompt(student_name, use_prompt_hint)
    transcribe_options = {
        "language": "ko",
        "beam_size": beam_size,
        "temperature": 0,
        "condition_on_previous_text": False,
        "task": "transcribe",
        "word_timestamps": False,
        "vad_filter": use_vad_filter,
    }
    if initial_prompt:
        transcribe_options["initial_prompt"] = initial_prompt
    if use_vad_filter:
        transcribe_options["vad_parameters"] = {"min_silence_duration_ms": 700}

    return transcribe_audio_with_wav_preprocess(
        audio_path=audio_path,
        model=model,
        transcribe_options=transcribe_options,
        use_vad_filter=use_vad_filter,
        processing_mode=processing_mode,
        progress_callback=progress_callback,
    )


def build_summary_prompt(
    student_name: str,
    counseling_date: date,
    transcript: str,
    summary_style: str,
    glossary_terms: list[dict[str, str | list[str]]] | None = None,
) -> str:
    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    style_instruction = SUMMARY_STYLES[summary_style]
    clean_name = student_name.strip()
    clean_date = counseling_date.isoformat()
    safe_transcript = transcript or ""
    glossary_section = ""
    glossary_rules = ""
    if glossary_terms:
        glossary_text = format_glossary_for_prompt(glossary_terms)
        if glossary_text:
            glossary_section = f"\n[학원 용어집]\n{glossary_text}\n"
            glossary_rules = (
                "\n[학원 용어집 사용 규칙]\n"
                "- 원문에 용어집의 term 또는 aliases와 비슷한 표현이 실제로 등장하면, 정리본에서는 공식 term으로 표기한다.\n"
                "- 예: 원문에 \"센\" 또는 \"쌘\"처럼 들리는 표현이 있으면 \"쎈\"으로 표기한다.\n"
                "- 예: 원문에 \"알피엠\"처럼 들리는 표현이 있으면 \"RPM\"으로 표기한다.\n"
                "- 원문에 전혀 등장하지 않은 교재명, 커리큘럼명, 시험명은 추측해서 넣지 않는다.\n"
                "- 용어집은 표기 보정을 위한 참고자료일 뿐이며, 새로운 상담 내용을 만들어내면 안 된다.\n"
                "- 애매하면 \"다음 수업 체크\"에 확인 항목으로 적는다.\n"
                "- 원문에 없는 내용은 절대 추가하지 않는다.\n"
            )
    return (
        f"{prompt_template.strip()}\n\n"
        f"[정리본 스타일]\n{summary_style}: {style_instruction}\n\n"
        f"{glossary_section}"
        "[입력 정보]\n"
        f"학생명: {clean_name}\n"
        f"상담일: {clean_date}\n\n"
        "[음성 인식 원문]\n"
        f"{safe_transcript.strip()}\n"
        "\n[작성 규칙]\n"
        "위 입력 정보의 학생명과 상담일은 식별용으로만 사용한다.\n"
        "- 최종 정리본 본문에는 학생명, 상담일, \"학생:\", \"학생명:\", \"상담일:\" 항목을 쓰지 않는다.\n"
        "- 녹음 원문에서 학생명이나 날짜를 다시 추정하지 않는다.\n"
        "- 원문에 다른 이름처럼 들리는 내용이 있어도 정리본 본문에 이름/날짜를 보충하지 않는다.\n"
        "- 원문에 없는 내용은 추측하지 않는다.\n"
        "- 정리본만 출력한다.\n"
        "- 가능하면 정리본 본문은 SUMMARY_BEGIN 다음 줄부터 SUMMARY_END 이전 줄까지만 작성한다.\n"
        "- SUMMARY_BEGIN/SUMMARY_END 밖에는 설명, 실행 로그, 명령 출력, 오류 진단을 쓰지 않는다.\n"
        f"{glossary_rules}"
    )


def format_transcript_file(student_name: str, counseling_date: date, transcript: str) -> str:
    return _format_transcript_file(student_name, counseling_date, transcript)


def normalize_summary_identity(summary: str, student_name: str, counseling_date: date) -> str:
    return _normalize_summary_identity(summary, student_name, counseling_date)


def move_today_check_section_to_top(summary: str) -> str:
    return _move_today_check_section_to_top(summary)


def normalize_legacy_check_sections(summary: str) -> str:
    return _normalize_legacy_check_sections(summary)


def merge_next_action_sections_into_notes(summary: str) -> str:
    return _merge_next_action_sections_into_notes(summary)


def remove_parent_message_sections(summary: str) -> str:
    return _remove_parent_message_sections(summary)


def remove_status_detail_labels(summary: str) -> str:
    return _remove_status_detail_labels(summary)


def remove_misc_record_section(summary: str) -> str:
    return _remove_misc_record_section(summary)


def remove_empty_placeholder_lines(summary: str) -> str:
    return _remove_empty_placeholder_lines(summary)


def remove_generic_today_focus_items(summary: str) -> str:
    return _remove_generic_today_focus_items(summary)


def remove_meta_uncertainty_notes(summary: str) -> str:
    return _remove_meta_uncertainty_notes(summary)


def ensure_required_today_focus_section(summary: str) -> str:
    return _ensure_required_today_focus_section(summary)


def format_summary_section_spacing(summary: str | None) -> str:
    return _format_summary_section_spacing(summary)


def summary_has_required_today_focus(summary: str | None) -> bool:
    return _summary_has_required_today_focus(summary)


def summary_has_required_notes(summary: str | None) -> bool:
    return _summary_has_required_notes(summary)


def summary_has_required_body_content(summary: str | None) -> bool:
    return bool(_summary_has_meaningful_body_content(summary))


def normalize_summary(summary: str, student_name: str, counseling_date: date) -> str:
    no_runtime_noise = remove_runtime_noise_from_summary(summary)
    identity_fixed = normalize_summary_identity(no_runtime_noise, student_name, counseling_date)
    no_parent_message = remove_parent_message_sections(identity_fixed)
    no_status_labels = remove_status_detail_labels(no_parent_message)
    no_misc_record = remove_misc_record_section(no_status_labels)
    no_empty_placeholders = remove_empty_placeholder_lines(no_misc_record)
    normalized_checks = normalize_legacy_check_sections(no_empty_placeholders)
    normalized_without_placeholders = remove_empty_placeholder_lines(normalized_checks)
    no_next_action_section = merge_next_action_sections_into_notes(normalized_without_placeholders)
    no_generic_today_focus = remove_generic_today_focus_items(no_next_action_section)
    no_meta_uncertainty = remove_meta_uncertainty_notes(no_generic_today_focus)
    with_required_today = ensure_required_today_focus_section(no_meta_uncertainty)
    return format_summary_section_spacing(move_today_check_section_to_top(with_required_today))


INVALID_SUMMARY_VALUES = {"c", "C", ".", "-", "없음", "null", "None", "none", "NULL"}
SUMMARY_STATUS_READY = "ready"
SUMMARY_STATUS_FAILED = "failed"
SUMMARY_STATUS_INSUFFICIENT_TRANSCRIPT = "insufficient_transcript"
SUMMARY_STATUS_NEEDS_COMPLETION = "needs_completion"
MIN_TRANSCRIPT_CHARS_FOR_AUTO_SUMMARY = 300
MIN_TRANSCRIPT_WORDS_FOR_AUTO_SUMMARY = 30
MIN_TRANSCRIPT_SENTENCES_FOR_AUTO_SUMMARY = 3


SUMMARY_INVALID_REASON_LABELS = {
    "empty": "빈 정리본",
    "placeholder": "의미 없는 정리본 값",
    "too_short": "너무 짧은 정리본",
    "too_short_without_structure": "섹션/항목 없는 짧은 정리본",
    "missing_today_focus": "[오늘 확인할 것] 누락",
    "missing_specific_today_check": "[오늘 확인할 것] 구체 확인 항목 부족",
    "missing_notes": "*상담 참고사항* 누락",
    "missing_content": "진도/숙제/상담 참고 내용 누락",
}


def count_meaningful_transcript_words(transcript: object) -> int:
    text = str(transcript or "")
    tokens = re.findall(r"[0-9A-Za-z가-힣]+", text)
    return sum(1 for token in tokens if len(token.strip()) >= 2)


def count_transcript_sentences_for_sufficiency(transcript: object) -> int:
    text = str(transcript or "").strip()
    if not text:
        return 0
    try:
        sentences = split_transcript_sentences(text)
    except Exception:
        sentences = []
    meaningful_sentences = [sentence for sentence in sentences if len(str(sentence).strip()) >= 8]
    if meaningful_sentences:
        return len(meaningful_sentences)
    fallback_parts = re.split(r"[.!?。！？\n]+", text)
    return sum(1 for part in fallback_parts if len(part.strip()) >= 8)


def transcript_sufficiency_diagnostics(transcript: object) -> dict[str, object]:
    text = str(transcript or "").strip()
    char_count = len(text)
    word_count = count_meaningful_transcript_words(text)
    sentence_count = count_transcript_sentences_for_sufficiency(text)
    failed_checks = [
        char_count < MIN_TRANSCRIPT_CHARS_FOR_AUTO_SUMMARY,
        word_count < MIN_TRANSCRIPT_WORDS_FOR_AUTO_SUMMARY,
        sentence_count < MIN_TRANSCRIPT_SENTENCES_FOR_AUTO_SUMMARY,
    ]
    insufficient = sum(1 for failed in failed_checks if failed) >= 2
    return {
        "transcript_length_chars": char_count,
        "transcript_word_count": word_count,
        "transcript_sentence_count": sentence_count,
        "insufficient_transcript": insufficient,
        "min_transcript_chars": MIN_TRANSCRIPT_CHARS_FOR_AUTO_SUMMARY,
        "min_transcript_words": MIN_TRANSCRIPT_WORDS_FOR_AUTO_SUMMARY,
        "min_transcript_sentences": MIN_TRANSCRIPT_SENTENCES_FOR_AUTO_SUMMARY,
    }


def is_insufficient_transcript_for_auto_summary(transcript: object) -> bool:
    return bool(transcript_sufficiency_diagnostics(transcript)["insufficient_transcript"])


def generated_summary_invalid_reason(summary: object) -> str:
    text = remove_runtime_noise_from_summary(summary)
    if not text:
        return "empty"
    if text in INVALID_SUMMARY_VALUES:
        return "placeholder"
    if len(text) <= 2:
        return "too_short"
    has_section = bool(re.search(r"(\[[^\]]+\]|\*[^*\n]+\*|상담\s*참고사항|숙제\s*:|진도\s*\()", text))
    has_bullet = bool(re.search(r"(?m)^\s*[-*]\s*\S+", text))
    if len(text) < 20 and not (has_section or has_bullet):
        return "too_short_without_structure"
    if not summary_has_required_body_content(text):
        return "missing_content"
    if not summary_has_required_notes(text):
        return "missing_notes"
    return ""


def summary_generation_output_state(
    summary: object,
    failed: bool = False,
    summary_status: str | None = None,
) -> dict[str, object]:
    cleaned_summary = remove_runtime_noise_from_summary(summary)
    invalid_reason = generated_summary_invalid_reason(cleaned_summary)
    needs_completion = False
    blocked_status = summary_status in {
        SUMMARY_STATUS_FAILED,
        SUMMARY_STATUS_INSUFFICIENT_TRANSCRIPT,
    }
    valid = (not failed) and (not blocked_status) and not invalid_reason
    display_summary = ensure_required_today_focus_section(cleaned_summary) if (valid or needs_completion) else ""
    text = format_summary_section_spacing(display_summary) if (valid or needs_completion) else ""
    return {
        "valid": valid,
        "failed": bool(failed),
        "needs_completion": bool(needs_completion),
        "summary_status": SUMMARY_STATUS_NEEDS_COMPLETION if needs_completion else (SUMMARY_STATUS_READY if valid else summary_status or ""),
        "invalid_reason": invalid_reason,
        "textarea_value": text,
        "copy_allowed": valid,
        "save_allowed": valid,
        "copy_text": text,
    }


def summary_invalid_reason_label(reason: str | None) -> str:
    return SUMMARY_INVALID_REASON_LABELS.get(str(reason or ""), str(reason or "") or "정리본 유효성 확인 실패")


def transcript_generation_signature(transcript: object) -> dict[str, object]:
    text = str(transcript or "")
    return {
        "transcript_length": len(text),
        "transcript_hash": hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:16],
    }


def summary_retry_progress_message(reason: str | None) -> str:
    label = summary_invalid_reason_label(reason)
    if reason == "empty":
        return "정리본 1차 결과가 비어 있어 저장된 원문으로 1회 재시도합니다."
    if reason in {"missing_today_focus", "missing_specific_today_check"}:
        return "정리본 구조가 불완전해 1회 재시도합니다."
    if reason == "missing_notes":
        return "정리본에 상담 참고사항이 누락되어 1회 재시도합니다."
    return f"정리본 1차 결과가 유효하지 않아 1회 재시도합니다. ({label})"


def build_summary_generation_diagnostics(
    *,
    attempts: list[dict[str, object]],
    transcript: str,
    transcript_source: str,
    source_transcript_path: Path | str | None = None,
    retry_context: str | None = None,
) -> dict[str, object]:
    first_attempt = attempts[0] if attempts else {}
    retry_attempts = attempts[1:]
    last_attempt = attempts[-1] if attempts else {}
    signature = transcript_generation_signature(transcript)
    diagnostics: dict[str, object] = {
        "first_attempt_valid": bool(first_attempt.get("valid")) if first_attempt else False,
        "retry_attempted": bool(retry_attempts),
        "retry_valid": any(bool(attempt.get("valid")) for attempt in retry_attempts),
        "runtime_noise_removed": any(bool(attempt.get("runtime_noise_removed")) for attempt in attempts),
        "removed_noise_line_count": sum(int(attempt.get("removed_noise_line_count") or 0) for attempt in attempts),
        "transcript_source": transcript_source,
        "transcript_length": signature["transcript_length"],
        "transcript_hash": signature["transcript_hash"],
        "invalid_reason": last_attempt.get("invalid_reason") or "",
        "attempt_count": len(attempts),
        "retry_context": retry_context or "",
        "source_transcript_path": str(source_transcript_path or ""),
        "attempts": attempts,
    }
    return diagnostics


def generate_summary_from_transcript(
    student_name: str,
    counseling_date: date,
    transcript: str,
    summary_style: str,
    use_academy_glossary: bool,
    *,
    max_invalid_retries: int = 1,
    source_transcript_path: Path | str | None = None,
    retry_context: str | None = None,
    transcript_source: str = "direct_transcript",
    summary_runner=None,
    on_retry=None,
) -> dict[str, object]:
    safe_transcript = str(transcript or "")
    glossary_for_prompt = load_academy_glossary()[:GLOSSARY_PROMPT_LIMIT] if use_academy_glossary else []
    prompt = build_summary_prompt(
        student_name,
        counseling_date,
        safe_transcript,
        summary_style,
        glossary_for_prompt,
    )
    runner = summary_runner or run_codex_summary
    attempts: list[dict[str, object]] = []
    max_attempts = max(1, int(max_invalid_retries or 0) + 1)
    last_normalized_summary = ""

    for attempt_number in range(1, max_attempts + 1):
        raw_summary = runner(prompt)
        raw_noise_count = runtime_noise_line_count(raw_summary)
        cleaned_raw_summary = remove_runtime_noise_from_summary(raw_summary)
        normalized_summary = normalize_summary(cleaned_raw_summary, student_name, counseling_date)
        last_normalized_summary = normalized_summary
        invalid_reason = generated_summary_invalid_reason(normalized_summary)
        attempt_info = {
            "attempt": attempt_number,
            "valid": not bool(invalid_reason),
            "invalid_reason": invalid_reason,
            "raw_length": len(str(raw_summary or "")),
            "runtime_noise_removed": raw_noise_count > 0,
            "removed_noise_line_count": raw_noise_count,
            "normalized_length": len(str(normalized_summary or "")),
            **transcript_generation_signature(safe_transcript),
        }
        attempts.append(attempt_info)
        if not invalid_reason:
            return {
                "summary": normalized_summary,
                "prompt": prompt,
                "diagnostics": build_summary_generation_diagnostics(
                    attempts=attempts,
                    transcript=safe_transcript,
                    transcript_source=transcript_source,
                    source_transcript_path=source_transcript_path,
                    retry_context=retry_context,
                ),
            }
        if attempt_number < max_attempts and on_retry:
            on_retry(invalid_reason)

    diagnostics = build_summary_generation_diagnostics(
        attempts=attempts,
        transcript=safe_transcript,
        transcript_source=transcript_source,
        source_transcript_path=source_transcript_path,
        retry_context=retry_context,
    )
    reason = str(diagnostics.get("invalid_reason") or "")
    error = WorkflowStepError(
        "정리본 생성",
        "Codex가 유효한 정리본을 반환하지 않았습니다. "
        f"사유: {summary_invalid_reason_label(reason)}. "
        "원문은 저장되어 있으나 정리본 복사는 하지 않았습니다.",
    )
    error.diagnostics = diagnostics
    error.prompt = prompt
    error.summary = last_normalized_summary
    raise error


def is_valid_generated_summary(summary: object) -> bool:
    return not generated_summary_invalid_reason(summary)


def validate_manual_summary_candidate(
    summary: object,
    student_name: str,
    counseling_date: date,
) -> dict[str, str]:
    cleaned = remove_runtime_noise_from_summary(summary)
    if not cleaned:
        return {"summary": "", "invalid_reason": "empty"}
    normalized = normalize_summary(cleaned, student_name, counseling_date)
    return {
        "summary": normalized,
        "invalid_reason": generated_summary_invalid_reason(normalized),
    }


def summary_copy_save_allowed(summary: object, summary_status: str | None = None, failed: bool = False) -> bool:
    state = summary_generation_output_state(summary, failed=failed, summary_status=summary_status)
    return bool(state.get("copy_allowed") and state.get("save_allowed"))


def clear_failed_summary_state() -> None:
    for key in list(st.session_state.keys()):
        if str(key).startswith("edited_latest_summary_"):
            st.session_state.pop(key, None)
    st.session_state["latest_summary"] = ""
    st.session_state["summary_generation_failed"] = True
    st.session_state["summary_status"] = SUMMARY_STATUS_FAILED
    st.session_state["summary_retry_available"] = bool(
        st.session_state.get("transcript_text") or st.session_state.get("current_transcript_path")
    )
    st.session_state["summary_generation_diagnostics"] = {}
    st.session_state["manual_summary_fallback_text"] = ""


def set_needs_completion_summary_state(
    summary: object,
    diagnostics: dict[str, object] | None = None,
) -> None:
    for key in list(st.session_state.keys()):
        if str(key).startswith("edited_latest_summary_"):
            st.session_state.pop(key, None)
    st.session_state["latest_summary"] = str(summary or "")
    st.session_state["summary_generation_failed"] = False
    st.session_state["summary_status"] = SUMMARY_STATUS_NEEDS_COMPLETION
    st.session_state["summary_retry_available"] = False
    st.session_state["summary_generation_diagnostics"] = diagnostics or {}


def clear_insufficient_transcript_summary_state(diagnostics: dict[str, object] | None = None) -> None:
    for key in list(st.session_state.keys()):
        if str(key).startswith("edited_latest_summary_"):
            st.session_state.pop(key, None)
    st.session_state["latest_summary"] = ""
    st.session_state["summary_generation_failed"] = False
    st.session_state["summary_status"] = SUMMARY_STATUS_INSUFFICIENT_TRANSCRIPT
    st.session_state["summary_retry_available"] = False
    st.session_state["summary_generation_diagnostics"] = diagnostics or {}


def extract_transcript_body_from_saved_text(content: object) -> str:
    text = str(content or "").strip()
    marker = "[음성 인식 원문]"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text


def read_saved_transcript_body(path_value: object) -> str:
    if not path_value:
        return ""
    try:
        path = Path(str(path_value))
        if not path.exists() or not path.is_file():
            return ""
        return extract_transcript_body_from_saved_text(path.read_text(encoding="utf-8"))
    except OSError:
        return ""


def generate_normalized_summary(
    student_name: str,
    counseling_date: date,
    transcript: str,
    summary_style: str,
    use_academy_glossary: bool,
) -> tuple[str, str]:
    result = generate_summary_from_transcript(
        student_name,
        counseling_date,
        transcript,
        summary_style,
        use_academy_glossary,
        max_invalid_retries=0,
        retry_context="single_attempt",
    )
    return str(result["summary"]), str(result["prompt"])


def fixed_windows_codex_path() -> Path:
    return _fixed_windows_codex_path()


def is_windowsapps_path(path: str | None) -> bool:
    return _is_windowsapps_path(path)


def codex_env() -> dict[str, str]:
    return _codex_env()


def find_codex_executable() -> dict[str, str]:
    executable = _find_codex_executable()
    fixed_path = fixed_windows_codex_path() if sys.platform.startswith("win") else None
    if executable is None:
        return {
            "kind": "",
            "path": "",
            "fixed_path": str(fixed_path or ""),
            "ignored_windowsapps": shutil.which("codex") or "",
        }
    return {
        "kind": executable.kind,
        "path": str(executable.path),
        "fixed_path": str(executable.fixed_path or ""),
        "ignored_windowsapps": executable.ignored_windowsapps,
    }


def codex_candidate_report() -> str:
    fixed_path = fixed_windows_codex_path() if sys.platform.startswith("win") else None
    lines = [
        f"고정 codex.cmd 경로: {fixed_path or '-'}",
        f"고정 codex.cmd 존재: {'예' if fixed_path and fixed_path.exists() else '아니오'}",
        f"shutil.which('codex'): {shutil.which('codex') or '-'}",
        f"WindowsApps codex 감지: {'예' if is_windowsapps_path(shutil.which('codex')) else '아니오'}",
    ]
    return "\n".join(lines)




CODEX_SUMMARY_MODEL = (
    os.environ.get("COUNSEL_NOTE_CODEX_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"
)
CODEX_SUMMARY_REASONING_EFFORT = (
    os.environ.get("COUNSEL_NOTE_CODEX_REASONING_EFFORT", "medium").strip() or "medium"
)

CODEX_INSTRUCTION = (
    "아래 표준입력의 상담 정보를 바탕으로 상담 정리본만 작성해. "
    "학생명과 상담일은 입력 정보 값을 그대로 사용해."
)


def build_codex_command(input_path: Path) -> list[str]:
    executable = _find_codex_executable()
    if executable is None:
        raise FileNotFoundError(
            "Codex CLI를 찾을 수 없습니다. 고정 npm 설치 경로를 확인하세요.\n\n"
            "[확인한 후보]\n"
            f"{codex_candidate_report()}\n\n"
            "[PowerShell 진단 명령]\n"
            "& \"$env:APPDATA\\npm\\codex.cmd\" --version"
        )

    invocations = _build_codex_invocations(
        executable=executable,
        model=CODEX_SUMMARY_MODEL,
        reasoning_effort=CODEX_SUMMARY_REASONING_EFFORT,
        instruction=CODEX_INSTRUCTION,
        prompt="",
        input_path=input_path,
    )
    return list(invocations[0].command)




def classify_codex_failure(stdout: str, stderr: str) -> str:
    category = _classify_codex_failure_category(stdout, stderr)
    if category == "usage_limit":
        return "Codex 사용량 한도에 걸렸습니다. 표시된 시간 이후 다시 시도하세요."
    if category == "authentication":
        return "Codex 로그인이 필요합니다. PowerShell에서 codex.cmd를 실행해 로그인하세요."
    if category == "network":
        return "Codex 네트워크 연결이 차단됐습니다. 앱을 정상 실행 경로에서 다시 시작하세요."
    if category in {"permission", "invocation"}:
        return "Codex CLI 실행 명령 또는 권한 문제입니다."
    if category == "empty_output":
        return "Codex가 빈 정리본을 반환했습니다."
    return "Codex CLI 실행 중 오류가 발생했습니다."


def format_command(command: list[str]) -> str:
    return " ".join(command)


def codex_login_status(executable: CodexExecutable | None = None) -> CodexLoginStatus:
    selected_executable = executable or _find_codex_executable()
    if selected_executable is None:
        return CodexLoginStatus("unknown", "Codex CLI executable was not found.", None)
    return _check_codex_login_status(
        selected_executable,
        cwd=BASE_DIR,
        env=codex_env(),
    )


def prepare_codex_summary_processing() -> bool:
    executable = _find_codex_executable()
    if executable is None:
        st.session_state["codex_login_required"] = False
        st.session_state["post_run_warning"] = ""
        st.session_state["post_run_error"] = (
            "Codex CLI를 찾지 못해 정리본 생성을 시작하지 않았습니다. 앱 설치 상태를 확인해 주세요."
        )
        return False

    login_status = codex_login_status(executable)
    if login_status.state == "not_authenticated":
        st.session_state["codex_login_required"] = True
        st.session_state["post_run_error"] = ""
        st.session_state["troubleshooting_info"] = None
        st.session_state["post_run_warning"] = (
            "Codex 로그인이 풀려 정리본 생성을 시작하지 않았습니다. 먼저 다시 로그인해 주세요."
        )
        return False
    if login_status.state == "authenticated":
        st.session_state["codex_login_required"] = False
    return True


def render_codex_login_recovery(ui_locked: bool) -> None:
    if not st.session_state.get("codex_login_required"):
        return
    st.info("Codex에 다시 로그인한 뒤 정리본 생성·복사를 누르면 저장된 작업부터 이어집니다.")
    if not st.button("Codex 로그인 열기", disabled=ui_locked, key="open_codex_login"):
        return
    executable = _find_codex_executable()
    if executable is None:
        st.error("Codex CLI를 찾지 못했습니다. 앱 설치 상태를 확인해 주세요.")
        return
    try:
        _launch_codex_login_window(
            executable,
            cwd=BASE_DIR,
            env=codex_env(),
        )
    except OSError as exc:
        st.error(f"Codex 로그인 창을 열지 못했습니다: {exc}")
        return
    st.success("열린 창에서 로그인을 완료한 뒤 정리본 생성·복사를 다시 누르세요.")


def run_codex_summary(prompt: str) -> str:
    executable = _find_codex_executable()
    if executable is None:
        build_codex_command(TMP_DIR / "codex_input.txt")
        raise FileNotFoundError("Codex CLI를 찾을 수 없습니다.")
    login_status = codex_login_status(executable)
    if login_status.state == "not_authenticated":
        raise RuntimeError(
            "codex exec 실행 실패:\n"
            "Codex 로그인이 필요합니다. 앱에서 Codex 로그인 열기를 누른 뒤 다시 시도하세요.\n"
            f"returncode: {login_status.returncode if login_status.returncode is not None else '-'}\n"
            f"stderr:\n{login_status.detail or 'Not logged in'}"
        )
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    input_path = TMP_DIR / "codex_input.txt"
    input_path.write_text(prompt, encoding="utf-8")
    invocations = _build_codex_invocations(
        executable=executable,
        model=CODEX_SUMMARY_MODEL,
        reasoning_effort=CODEX_SUMMARY_REASONING_EFFORT,
        instruction=CODEX_INSTRUCTION,
        prompt=prompt,
        input_path=input_path,
    )

    try:
        completed = run_codex_invocations(
            invocations,
            cwd=BASE_DIR,
            env=codex_env(),
            timeout_seconds=600,
        )
    except CodexExecutionError as exc:
        failure_type = classify_codex_failure(exc.stdout, exc.stderr)
        raise RuntimeError(
            "codex exec 실행 실패:\n"
            f"{failure_type}\n"
            f"선택된 실행 파일: {executable.path}\n"
            f"실행 방식: {exc.invocation_kind or '-'}\n"
            f"대체 실행 포함 시도 수: {exc.attempt_count}\n"
            f"실행 명령: {format_command(list(exc.command))}\n"
            f"returncode: {exc.returncode if exc.returncode is not None else '-'}\n"
            f"stdout:\n{exc.stdout.strip() or '-'}\n"
            f"stderr:\n{exc.stderr.strip() or '-'}\n"
            f"WindowsApps 무시: {'예' if executable.ignored_windowsapps or is_windowsapps_path(shutil.which('codex')) else '아니오'}"
        ) from exc
    finally:
        try:
            input_path.unlink(missing_ok=True)
        except OSError:
            pass

    summary = remove_runtime_noise_from_summary(extract_marked_summary_output(completed.stdout)).strip()
    if not summary or len(summary) < 20:
        raise RuntimeError(
            "Codex가 빈 정리본을 반환했습니다. 원문은 저장되어 있으나 정리본 복사는 하지 않았습니다.\n"
            f"선택된 실행 파일: {executable.path}\n"
            f"실행 방식: {completed.invocation_kind}\n"
            f"대체 실행 포함 시도 수: {completed.attempt_count}\n"
            f"실행 명령: {format_command(list(completed.command))}\n"
            f"returncode: {completed.returncode}\n"
            f"stdout 길이: {len(completed.stdout or '')}\n"
            f"stderr 길이: {len(completed.stderr or '')}\n"
            f"stderr:\n{completed.stderr.strip() or '-'}"
        )

    return summary




def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def is_m4a_or_mp3(path: Path | str | None) -> bool:
    if not path:
        return False
    return Path(str(path)).suffix.lower() in {".m4a", ".mp3"}


def ffmpeg_missing_audio_warning() -> str:
    return "ffmpeg를 찾을 수 없습니다. m4a/mp3 원문 추출이 실패하면 ffmpeg 설치가 필요합니다."




def sound_recording_candidate_dirs() -> list[Path]:
    home = Path.home()
    return [
        home / "Documents" / "Sound recordings",
        home / "OneDrive" / "Documents" / "Sound recordings",
        home / "Documents" / "소리 녹음",
        home / "OneDrive" / "Documents" / "소리 녹음",
        home / "문서" / "Sound recordings",
        home / "OneDrive" / "문서" / "Sound recordings",
        home / "문서" / "소리 녹음",
        home / "OneDrive" / "문서" / "소리 녹음",
        home / "Music" / "Sound recordings",
        home / "OneDrive" / "Music" / "Sound recordings",
        home / "Music" / "소리 녹음",
        home / "OneDrive" / "Music" / "소리 녹음",
    ]


def recording_candidate_dirs() -> list[Path]:
    return sound_recording_candidate_dirs()


def path_is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def recording_cleanup_roots() -> list[Path]:
    return [UPLOAD_DIR, *sound_recording_candidate_dirs()]


def protected_recording_paths() -> set[Path]:
    protected: set[Path] = set()
    keys = [
        "current_audio_path",
        "selected_audio_path",
        "uploaded_audio_saved_path",
        "recent_recording_path",
    ]
    for key in keys:
        value = st.session_state.get(key)
        if value:
            try:
                protected.add(Path(str(value)).resolve())
            except OSError:
                continue

    snapshot = st.session_state.get("current_job_snapshot") or {}
    snapshot_audio_path = snapshot.get("audio_path") if isinstance(snapshot, dict) else None
    if snapshot_audio_path:
        try:
            protected.add(Path(str(snapshot_audio_path)).resolve())
        except OSError:
            pass
    batch_state = st.session_state.get("audio_batch_state") or {}
    batch_jobs = batch_state.get("jobs", []) if isinstance(batch_state, dict) else []
    for job in batch_jobs:
        if not isinstance(job, dict) or job.get("status") not in {"pending", "processing"}:
            continue
        audio_path = job.get("audio_path")
        if not audio_path:
            continue
        try:
            protected.add(Path(str(audio_path)).resolve())
        except OSError:
            continue
    return protected


def is_safe_recording_cleanup_target(path: Path, roots: list[Path]) -> bool:
    if path.suffix.lower().lstrip(".") not in RECORDING_CLEANUP_EXTENSIONS:
        return False
    try:
        resolved_path = path.resolve()
    except OSError:
        return False
    for root in roots:
        if root.exists() and path_is_inside(resolved_path, root):
            return True
    return False


def iter_cleanup_recording_files(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    try:
        return [
            path for path in folder.iterdir()
            if path.is_file() and path.suffix.lower().lstrip(".") in RECORDING_CLEANUP_EXTENSIONS
        ]
    except OSError:
        return []


def file_modified_date_kst(path: Path) -> date | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo("Asia/Seoul")).date()
    except OSError:
        return None


def cleanup_old_recording_files() -> dict:
    today_kst = get_today_kst()
    result = {
        "cleaned_at": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S"),
        "cleanup_rule": f"{today_kst.isoformat()} 이전 날짜 녹음 파일",
        "uploads_moved": 0,
        "recorder_moved": 0,
        "failed": 0,
        "skipped_protected": 0,
        "candidate_roots": [],
        "errors": [],
    }
    if st.session_state.get("is_processing", False):
        result["errors"].append("processing")
        return result

    try:
        from send2trash import send2trash
    except Exception as exc:
        result["errors"].append(f"send2trash unavailable: {exc}")
        return result

    roots = recording_cleanup_roots()
    protected = protected_recording_paths()

    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        result["candidate_roots"].append(str(root))
        is_upload_root = path_is_inside(root, UPLOAD_DIR) or root.resolve() == UPLOAD_DIR.resolve()
        for path in iter_cleanup_recording_files(root):
            try:
                resolved_path = path.resolve()
                if resolved_path in protected:
                    result["skipped_protected"] += 1
                    continue
                if not is_safe_recording_cleanup_target(resolved_path, roots):
                    continue
                modified_date = file_modified_date_kst(path)
                if modified_date is None or modified_date >= today_kst:
                    continue
                send2trash(str(resolved_path))
                if is_upload_root:
                    result["uploads_moved"] += 1
                else:
                    result["recorder_moved"] += 1
            except Exception as exc:
                result["failed"] += 1
                result["errors"].append(f"{path}: {exc}")
    return result


def protected_app_cleanup_paths() -> set[Path]:
    protected = protected_recording_paths()
    for key in [
        "current_transcript_path",
        "uploaded_audio_saved_path",
        "current_audio_path",
        "selected_audio_path",
    ]:
        value = st.session_state.get(key)
        if value:
            try:
                protected.add(Path(str(value)).resolve())
            except OSError:
                continue
    return protected


def iter_app_cleanup_files(root: Path, recursive: bool) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    try:
        iterator = root.rglob("*") if recursive else root.iterdir()
        return [path for path in iterator if path.is_file()]
    except OSError:
        return []


def is_safe_app_cleanup_target(
    path: Path,
    root: Path,
    allowed_extensions: set[str] | None,
) -> bool:
    try:
        resolved_path = path.resolve()
        resolved_root = root.resolve()
        resolved_transcripts = TRANSCRIPT_DIR.resolve()
        resolved_students = STUDENTS_DIR.resolve()
    except OSError:
        return False
    if not path_is_inside(resolved_path, resolved_root):
        return False
    if path_is_inside(resolved_path, resolved_transcripts):
        return False
    if path_is_inside(resolved_path, resolved_students):
        return False
    if allowed_extensions is not None:
        extension = resolved_path.suffix.lower().lstrip(".")
        if extension not in allowed_extensions:
            return False
    return True


def cleanup_app_maintenance_files() -> dict:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    result = {
        "cleaned_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "moved": 0,
        "failed": 0,
        "skipped_protected": 0,
        "policies": [],
        "errors": [],
    }
    if st.session_state.get("is_processing", False):
        result["errors"].append("processing")
        return result

    try:
        from send2trash import send2trash
    except Exception as exc:
        result["errors"].append(f"send2trash unavailable: {exc}")
        return result

    protected = protected_app_cleanup_paths()
    for policy in APP_MAINTENANCE_CLEANUP_POLICIES:
        root = policy["root"]
        days = int(policy["days"])
        allowed_extensions = policy["extensions"]
        recursive = bool(policy["recursive"])
        cutoff = now.timestamp() - (days * 24 * 60 * 60)
        moved_for_policy = 0
        checked_for_policy = 0
        for path in iter_app_cleanup_files(root, recursive):
            checked_for_policy += 1
            try:
                resolved_path = path.resolve()
                if resolved_path in protected:
                    result["skipped_protected"] += 1
                    continue
                if not is_safe_app_cleanup_target(resolved_path, root, allowed_extensions):
                    continue
                if resolved_path.stat().st_mtime >= cutoff:
                    continue
                send2trash(str(resolved_path))
                moved_for_policy += 1
                result["moved"] += 1
            except Exception as exc:
                result["failed"] += 1
                result["errors"].append(f"{path}: {exc}")
        result["policies"].append(
            {
                "name": policy["name"],
                "root": str(root),
                "days": days,
                "checked": checked_for_policy,
                "moved": moved_for_policy,
            }
        )
    return result


def run_app_maintenance_cleanup_once_per_session() -> None:
    if st.session_state.get("app_maintenance_cleanup_done_this_session"):
        return
    st.session_state["app_maintenance_cleanup_result"] = cleanup_app_maintenance_files()
    st.session_state["app_maintenance_cleanup_done_this_session"] = True


def run_recording_cleanup_once_per_session() -> None:
    if st.session_state.get("recording_cleanup_done_this_session"):
        return
    if not _recording_auto_cleanup_enabled(BASE_DIR):
        st.session_state.pop("recording_cleanup_result", None)
        st.session_state["recording_cleanup_done_this_session"] = True
        return
    st.session_state["recording_cleanup_result"] = cleanup_old_recording_files()
    st.session_state["recording_cleanup_done_this_session"] = True




def copy_to_clipboard(text: str) -> tuple[bool, str | None]:
    if not text or not text.strip():
        return False, "복사할 정리본이 비어 있습니다."
    try:
        pyperclip.copy(text)
        return True, None
    except pyperclip.PyperclipException as exc:
        return False, str(exc)
    except Exception as exc:  # pyperclip may raise platform-specific errors.
        return False, str(exc)


def normalize_legacy_today_task_ui_terms(message: object) -> str:
    text = str(message or "")
    legacy_label = "오늘 " + "할 " + "일"
    legacy_compact = "오늘" + "할" + "일"
    legacy_no_mid_space = "오늘 " + "할" + "일"
    replacements = {
        f"{legacy_label}이 클립보드에 복사되었습니다": "정리본이 클립보드에 복사되었습니다",
        f"{legacy_label} 복사 완료": "정리본 전체 복사 완료",
        f"{legacy_label} 생성 완료": "정리본 생성 완료",
        legacy_label: "오늘 확인할 것",
        legacy_compact: "오늘 확인할 것",
        legacy_no_mid_space: "오늘 확인할 것",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def extract_student_name_from_audio_filename(file_name: str | Path | None) -> str | None:
    return _extract_student_name_from_audio_filename(file_name)


def queue_student_name_from_audio_filename(file_name: str | Path | None) -> None:
    if st.session_state.get("is_processing", False):
        return
    detected_name = extract_student_name_from_audio_filename(file_name)
    st.session_state["detected_student_name"] = detected_name or ""
    if not detected_name:
        st.session_state["student_name_detection_message"] = "파일명에서 학생명을 찾지 못했습니다. 직접 입력하세요."
        return
    st.session_state["student_name_detection_message"] = ""
    st.session_state["pending_student_name_from_file"] = detected_name


def handle_audio_upload_change() -> None:
    upload_key = st.session_state.get("audio_upload_key", "audio_upload")
    uploaded_files = st.session_state.get(upload_key) or []
    if len(uploaded_files) == 1:
        queue_student_name_from_audio_filename(uploaded_files[0].name)
        return
    st.session_state["detected_student_name"] = ""
    st.session_state["student_name_detection_message"] = ""
    st.session_state["current_audio_path"] = None
    st.session_state["selected_audio_path"] = None
    st.session_state["uploaded_audio_key"] = ""
    st.session_state["uploaded_audio_saved_path"] = ""
    st.session_state.pop("pending_student_name_from_file", None)
    st.session_state.pop("recent_recording_path", None)


def clear_selected_audio_after_success() -> None:
    next_index = st.session_state.get("audio_upload_reset_index", 0) + 1
    st.session_state["audio_upload_reset_index"] = next_index
    st.session_state["audio_upload_key"] = f"audio_upload_{next_index}"
    st.session_state["current_audio_path"] = None
    st.session_state["selected_audio_path"] = None
    st.session_state["uploaded_audio_key"] = ""
    st.session_state["uploaded_audio_saved_path"] = ""
    st.session_state.pop("recent_recording_path", None)


def apply_pending_student_name_from_file() -> None:
    if st.session_state.get("is_processing", False):
        return
    pending_name = st.session_state.pop("pending_student_name_from_file", None)
    batch_state = st.session_state.get("audio_batch_state")
    summary = st.session_state.get("latest_summary") or ""
    transcript_path = st.session_state.get("current_transcript_path") or ""
    if batch_state and summary and transcript_path and (pending_name or not st.session_state.get("student_name")):
        counseling_date = st.session_state.get("counseling_date", get_today_kst())
        author_name = st.session_state.get("author_name", get_default_author_name())
        matching_jobs = [
            job for job in batch_state["jobs"]
            if job["status"] in {"completed", "needs_review"}
            and job["summary"] == summary
            and job["transcript_path"] == transcript_path
            and job["counseling_date"] == counseling_date.isoformat()
        ]
        if len(matching_jobs) == 1 and (not pending_name or pending_name == matching_jobs[0]["student_name"]):
            summary_student = matching_jobs[0]["student_name"]
            st.session_state["student_name"] = summary_student
            st.session_state["form_transcript_key"] = (
                f"{counseling_date.isoformat()}|{sanitize_filename_part(summary_student)}|"
                f"{sanitize_filename_part(author_name)}"
            )
    if pending_name:
        st.session_state["student_name"] = pending_name


def validate_uploaded_extension(uploaded_file) -> None:
    extension = Path(uploaded_file.name).suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("mp3, m4a, wav 파일만 업로드할 수 있습니다.")


def safe_upload_path(filename: str) -> Path:
    original = Path(filename)
    safe_stem = sanitize_filename_part(original.stem)
    suffix = original.suffix.lower()
    candidate = UPLOAD_DIR / f"{safe_stem}{suffix}"
    if not candidate.exists():
        return candidate

    for index in range(2, 1000):
        numbered = UPLOAD_DIR / f"{safe_stem}_{index}{suffix}"
        if not numbered.exists():
            return numbered

    raise RuntimeError("사용 가능한 업로드 파일명을 만들 수 없습니다.")


def save_uploaded_file_copy(uploaded_file) -> Path:
    validate_uploaded_extension(uploaded_file)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_key = f"{uploaded_file.name}|{uploaded_file.size}"
    if (
        st.session_state.get("uploaded_audio_key") == upload_key
        and st.session_state.get("uploaded_audio_saved_path")
        and Path(st.session_state["uploaded_audio_saved_path"]).exists()
    ):
        cached_path = Path(st.session_state["uploaded_audio_saved_path"])
        st.session_state["current_audio_path"] = str(cached_path)
        return cached_path

    destination = safe_upload_path(uploaded_file.name)
    destination.write_bytes(uploaded_file.getbuffer())
    st.session_state["uploaded_audio_key"] = upload_key
    st.session_state["uploaded_audio_saved_path"] = str(destination)
    st.session_state["current_audio_path"] = str(destination)
    return destination


def save_audio_batch_upload_copy(uploaded_file) -> Path:
    validate_uploaded_extension(uploaded_file)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination = safe_upload_path(uploaded_file.name)
    destination.write_bytes(uploaded_file.getbuffer())
    return destination


def audio_batch_now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def persist_audio_batch_session_state(state: AudioBatchState) -> None:
    save_audio_batch_state(AUDIO_BATCH_STATE_PATH, state)
    st.session_state["audio_batch_state"] = state


def clear_stalled_audio_batch_runtime_flags() -> None:
    st.session_state["is_processing"] = False
    st.session_state["pending_main_run"] = False
    st.session_state["current_job_snapshot"] = None


def initialize_audio_batch_session_state() -> None:
    if st.session_state.get("audio_batch_initialized"):
        return
    state = load_audio_batch_state(AUDIO_BATCH_STATE_PATH)
    if state is None:
        if AUDIO_BATCH_STATE_PATH.exists():
            st.session_state["audio_batch_load_warning"] = (
                "이전 일괄 처리 상태를 읽지 못했습니다. 새 일괄 처리를 시작할 수 있습니다."
            )
        st.session_state["audio_batch_state"] = None
    elif state.get("acknowledged_at") and audio_batch_all_saved(state):
        st.session_state["audio_batch_state"] = None
        if not delete_audio_batch_state(AUDIO_BATCH_STATE_PATH):
            st.session_state["audio_batch_load_warning"] = (
                "완료된 이전 일괄 처리 상태 파일을 정리하지 못했습니다."
            )
    else:
        reconciled = reconcile_audio_batch_paths(state, audio_batch_now_iso())
        if reconciled["status"] == "running":
            recovered = recover_interrupted_audio_batch_state(reconciled, audio_batch_now_iso())
        else:
            recovered = reconciled
        persist_audio_batch_session_state(recovered)
        if recovered != state:
            st.session_state["audio_batch_recovered"] = True
    st.session_state["audio_batch_initialized"] = True


def audio_batch_job_snapshot(job: AudioBatchJob, position: int, total: int) -> dict[str, object]:
    transcript_path = job.get("transcript_path", "")
    return {
        "student_name": job["student_name"],
        "author_name": job["author_name"],
        "counseling_date": job["counseling_date"],
        "audio_path": job["audio_path"],
        "processing_mode": job["processing_mode"],
        "summary_style": job["summary_style"],
        "use_academy_glossary": True,
        "use_vad": job["use_vad"],
        "use_prompt_hint": job["use_prompt_hint"],
        "batch_job_id": job["job_id"],
        "batch_position": position,
        "batch_total": total,
        "resume_summary_only": bool(transcript_path and Path(transcript_path).is_file()),
        "resume_transcript_path": transcript_path,
        "resume_admin_save_only": bool(job["summary"].strip()),
    }


def start_next_audio_batch_job() -> bool:
    state = st.session_state.get("audio_batch_state")
    if not state:
        return False
    counts = audio_batch_counts(state)
    if counts["processing"] > 0:
        return False
    next_job = next_pending_audio_batch_job(state)
    if next_job is None:
        completed_state = set_audio_batch_status(state, "completed", audio_batch_now_iso())
        persist_audio_batch_session_state(completed_state)
        counts = audio_batch_counts(completed_state)
        st.session_state["is_processing"] = False
        st.session_state["pending_main_run"] = False
        st.session_state["current_job_snapshot"] = None
        st.session_state["post_run_toast"] = (
            f"녹음 일괄 {'저장 완료' if audio_batch_all_saved(completed_state) else '저장 미완료'}. "
            f"ADMIN 저장 완료 {audio_batch_saved_student_count(completed_state)}명 · "
            f"확인 필요 {counts['needs_review']}건 · 실패 {counts['failed']}건"
        )
        st.session_state["post_run_success"] = ""
        return False

    index, job = next_job
    processing_state = update_audio_batch_job(
        state,
        job["job_id"],
        status="processing",
        detail=f"{index + 1}/{state['total']} 처리 중",
        transcript_path=job["transcript_path"],
        summary=job["summary"],
        updated_at=audio_batch_now_iso(),
        increment_attempts=True,
    )
    persist_audio_batch_session_state(processing_state)
    st.session_state["current_audio_path"] = job["audio_path"]
    st.session_state["selected_audio_path"] = job["audio_path"]
    st.session_state["current_job_snapshot"] = audio_batch_job_snapshot(
        job,
        index + 1,
        state["total"],
    )
    st.session_state["latest_summary"] = ""
    st.session_state["summary_generation_failed"] = False
    st.session_state["summary_status"] = ""
    st.session_state["summary_retry_available"] = False
    st.session_state["summary_generation_diagnostics"] = {}
    st.session_state["post_run_success"] = ""
    st.session_state["post_run_toast"] = ""
    st.session_state["post_run_warning"] = ""
    st.session_state["post_run_error"] = ""
    st.session_state["is_processing"] = True
    st.session_state["pending_main_run"] = True
    return True


def start_next_audio_batch_job_with_refresh() -> bool:
    if not start_next_audio_batch_job():
        return False
    st.rerun()
    return True


def continue_running_audio_batch_if_needed() -> bool:
    state = st.session_state.get("audio_batch_state")
    if not state or state["status"] != "running":
        return False
    if st.session_state.get("pending_main_run"):
        return False
    counts = audio_batch_counts(state)
    if counts["processing"] > 0 or counts["pending"] == 0:
        return False
    return start_next_audio_batch_job()


def checkpoint_active_audio_batch_transcript(transcript_path: Path) -> None:
    snapshot = st.session_state.get("current_job_snapshot") or {}
    job_id = str(snapshot.get("batch_job_id") or "")
    state = st.session_state.get("audio_batch_state")
    if not job_id or not state:
        return
    current = next((job for job in state["jobs"] if job["job_id"] == job_id), None)
    if current is None:
        return
    checkpointed = update_audio_batch_job(
        state,
        job_id,
        status="processing",
        detail="원문 저장 완료 · 정리본 생성 대기",
        transcript_path=str(transcript_path),
        summary=current["summary"],
        updated_at=audio_batch_now_iso(),
    )
    persist_audio_batch_session_state(checkpointed)


def audio_batch_all_saved(state: AudioBatchState) -> bool:
    return all(job["status"] == "completed" and job.get("admin_saved", False) for job in state["jobs"])


def audio_batch_saved_student_count(state: AudioBatchState) -> int:
    return len({job["admin_sid"] for job in state["jobs"] if job.get("admin_saved") and job.get("admin_sid")})


def save_audio_batch_job_to_admin(job_id: str) -> None:
    state = st.session_state["audio_batch_state"]
    job = next(job for job in state["jobs"] if job["job_id"] == job_id)
    if job.get("admin_saved"):
        return
    counseling_date = date.fromisoformat(job["counseling_date"])
    export_text, source_label = admin_notes_export_text_from_summary(summary_text=job["summary"])
    base_url = load_admin_api_base_url()
    candidates = find_admin_sid_candidates_for_student(job["student_name"], base_url)
    candidate = find_admin_sid_candidate_by_sid(candidates, job.get("admin_sid", ""))
    if candidate is None and len(candidates) == 1:
        candidate = candidates[0]
    if candidate is None:
        override = get_local_admin_sid_override(job["student_name"])
        candidate = find_admin_sid_candidate_by_sid(candidates, override.get("selected_admin_sid", ""))
    if candidate is None:
        candidate, _ = auto_select_admin_sid_candidate(
            candidates, build_admin_sid_auto_match_context(export_text, counseling_date),
        )
    admin_sid = (candidate or {}).get("admin_sid", "")
    block_message = admin_notes_export_block_message(
        student_name=job["student_name"], export_text=export_text, admin_sid=admin_sid,
        export_allowed=summary_copy_save_allowed(job["summary"], SUMMARY_STATUS_READY),
    )
    pending = update_audio_batch_job(
        state, job_id, status="needs_review", detail=block_message or "정리본 생성 완료 · ADMIN 저장 확인 대기",
        transcript_path=job["transcript_path"], summary=job["summary"], updated_at=audio_batch_now_iso(),
    )
    pending_job = next(item for item in pending["jobs"] if item["job_id"] == job_id)
    pending_job["admin_sid"] = admin_sid
    pending_job["admin_saved"] = False
    persist_audio_batch_session_state(pending)
    if block_message:
        return
    request = AdminNotesSyncRequest(
        student_name=job["student_name"], counseling_date=counseling_date, admin_sid=admin_sid,
        admin_base_url=base_url, export_text=export_text, export_source_label=source_label,
        today_focus_text="", preview_key=admin_notes_preview_key(job["student_name"], counseling_date, admin_sid, export_text),
    )
    try:
        result = sync_admin_notes(request)
    except (WorkflowStepError, OSError) as exc:
        pending_job["detail"] = f"정리본 생성 완료 · ADMIN 저장 미완료: {str(exc).splitlines()[0][:160]}"
        persist_audio_batch_session_state(pending)
        return
    saved = update_audio_batch_job(
        pending, job_id, status="completed", detail="ADMIN 저장 완료" if result.updated else "ADMIN 저장 확인 완료 · 이미 저장됨",
        transcript_path=job["transcript_path"], summary=job["summary"], updated_at=audio_batch_now_iso(),
    )
    next(item for item in saved["jobs"] if item["job_id"] == job_id)["admin_saved"] = True
    persist_audio_batch_session_state(saved)
    st.session_state["last_admin_notes_save_key"] = request.preview_key


def save_completed_audio_batch_summaries() -> None:
    state = st.session_state["audio_batch_state"]
    for job in state["jobs"]:
        if job["summary"].strip() and job["status"] in {"completed", "needs_review"} and not job.get("admin_saved"):
            save_audio_batch_job_to_admin(job["job_id"])


def finish_active_audio_batch_job(
    *,
    status: AudioBatchJobStatus,
    detail: str,
    transcript_path: str,
    summary: str,
    pause_batch: bool,
) -> None:
    snapshot = st.session_state.get("current_job_snapshot") or {}
    job_id = str(snapshot.get("batch_job_id") or "")
    state = st.session_state.get("audio_batch_state")
    if not job_id or not state:
        st.session_state["is_processing"] = False
        st.session_state["pending_main_run"] = False
        return
    updated = update_audio_batch_job(
        state,
        job_id,
        status=status,
        detail=detail,
        transcript_path=transcript_path,
        summary=summary,
        updated_at=audio_batch_now_iso(),
    )
    if pause_batch:
        updated = set_audio_batch_status(updated, "paused", audio_batch_now_iso())
    persist_audio_batch_session_state(updated)
    if summary.strip() and status in {"completed", "needs_review"}:
        completed_job = next(job for job in updated["jobs"] if job["job_id"] == job_id)
        st.session_state["pending_student_name_from_file"] = completed_job["student_name"]
    st.session_state["is_processing"] = False
    st.session_state["pending_main_run"] = False
    st.session_state["current_job_snapshot"] = None
    if pause_batch:
        st.session_state["post_run_warning"] = (
            "Codex를 사용할 수 없어 일괄 처리를 멈췄습니다. 로그인 또는 사용량 상태를 확인한 뒤 "
            "남은 작업 이어서 처리를 누르세요."
        )
        return


def audio_batch_download_text(state: AudioBatchState) -> str:
    blocks = []
    for job in state["jobs"]:
        summary = job.get("summary", "").strip()
        if not summary:
            continue
        blocks.append(f"[{job['student_name']} · {job['source_name']}]\n{summary}")
    return "\n\n".join(blocks).strip()


def is_codex_usage_limit_error(error: object) -> bool:
    text = str(error or "").lower()
    return any(
        phrase in text
        for phrase in [
            "usage limit",
            "you've hit your usage limit",
            "try again at",
            "codex 사용량 한도",
        ]
    )


def is_codex_authentication_error(error: object) -> bool:
    text = str(error or "").strip()
    return (
        _classify_codex_failure_category("", text) == "authentication"
        or "codex 로그인이 필요" in text.casefold()
    )


def is_codex_network_error(error: object) -> bool:
    return _classify_codex_failure_category("", str(error or "")) == "network"


def should_pause_audio_batch_for_codex_error(error: object) -> bool:
    return (
        is_codex_usage_limit_error(error)
        or is_codex_authentication_error(error)
        or is_codex_network_error(error)
    )


def load_draft() -> dict:
    if not CURRENT_DRAFT_PATH.exists():
        return {}
    try:
        return json.loads(CURRENT_DRAFT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def parse_draft_counseling_date(value: object, fallback: date) -> date:
    return _parse_draft_counseling_date(value, fallback)


def save_draft(last_step: str | None = None) -> None:
    try:
        DRAFT_DIR.mkdir(parents=True, exist_ok=True)
        counseling_date = st.session_state.get("counseling_date", get_today_kst())
        if isinstance(counseling_date, date):
            counseling_date_value = counseling_date.isoformat()
        else:
            counseling_date_value = str(counseling_date)

        draft = {
            "student_name": st.session_state.get("student_name", ""),
            "author_name": st.session_state.get("author_name", get_default_author_name()),
            "counseling_date": counseling_date_value,
            "selected_mode": st.session_state.get("selected_mode", "균형 모드"),
            "selected_summary_style": st.session_state.get("selected_summary_style", "기본 상담 정리"),
            "transcript_text": st.session_state.get("transcript_text", ""),
            "transcript_file_path": st.session_state.get("current_transcript_path", ""),
            "uploaded_audio_saved_path": st.session_state.get("uploaded_audio_saved_path", ""),
            "current_audio_path": st.session_state.get("current_audio_path", ""),
            "last_step": last_step or st.session_state.get("last_step", ""),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        st.session_state["last_step"] = draft["last_step"]
        CURRENT_DRAFT_PATH.write_text(
            json.dumps(draft, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        st.session_state.pop("draft_save_error", None)
    except OSError as exc:
        st.session_state["draft_save_error"] = str(exc)


def initialize_draft_state() -> None:
    if st.session_state.get("draft_initialized"):
        return

    draft = load_draft()
    today_kst = get_today_kst()
    if draft:
        restored_date = parse_draft_counseling_date(
            draft.get("counseling_date"),
            today_kst,
        )
        st.session_state["draft_restored"] = True
        st.session_state["student_name"] = draft.get("student_name", "")
        st.session_state["author_name"] = draft.get("author_name", get_default_author_name())
        st.session_state["counseling_date"] = today_kst
        st.session_state["counseling_date_input"] = today_kst
        st.session_state["draft_restored_student_name"] = st.session_state["student_name"]
        st.session_state["draft_restored_counseling_date"] = restored_date.isoformat()

        selected_mode = draft.get("selected_mode", "균형 모드")
        selected_style = draft.get("selected_summary_style", "기본 상담 정리")
        st.session_state["selected_mode"] = (
            selected_mode if selected_mode in PROCESSING_MODES else "균형 모드"
        )
        st.session_state["selected_summary_style"] = (
            selected_style if selected_style in SUMMARY_STYLES else "기본 상담 정리"
        )
        st.session_state["transcript_text"] = draft.get("transcript_text", "")
        st.session_state["current_transcript_path"] = draft.get("transcript_file_path", "")
        st.session_state["uploaded_audio_saved_path"] = draft.get("uploaded_audio_saved_path", "")
        st.session_state["current_audio_path"] = draft.get(
            "current_audio_path",
            draft.get("uploaded_audio_saved_path", ""),
        )
        st.session_state["last_step"] = draft.get("last_step", "")
        if st.session_state["student_name"]:
            st.session_state["form_transcript_key"] = (
                f"{st.session_state['counseling_date'].isoformat()}|"
                f"{sanitize_filename_part(st.session_state['student_name'])}"
            )
    else:
        st.session_state.setdefault("student_name", "")
        st.session_state.setdefault("author_name", get_default_author_name())
        st.session_state.setdefault("counseling_date", today_kst)
        st.session_state.setdefault("counseling_date_input", st.session_state["counseling_date"])
        st.session_state.setdefault("selected_mode", "GPU 터보 모드")
        st.session_state.setdefault("selected_summary_style", "기본 상담 정리")
        st.session_state.setdefault("transcript_text", "")
        st.session_state.setdefault("transcript_segments", [])
        st.session_state.setdefault("segment_rows", [])
        st.session_state.setdefault("current_transcript_path", "")
        st.session_state.setdefault("uploaded_audio_saved_path", "")
        st.session_state.setdefault("current_audio_path", None)
        st.session_state.setdefault("selected_audio_path", None)
        st.session_state.setdefault("last_step", "")
        st.session_state.setdefault("latest_summary", "")
        st.session_state.setdefault("is_processing", False)
        st.session_state.setdefault("pending_main_run", False)
        st.session_state.setdefault("current_job_snapshot", None)
        st.session_state.setdefault("last_error", None)
        st.session_state.setdefault("last_error_details", {})

    st.session_state.setdefault("latest_summary", "")
    st.session_state.setdefault("transcript_text", "")
    st.session_state.setdefault("transcript_segments", [])
    st.session_state.setdefault("segment_rows", [])
    st.session_state.setdefault("counseling_date", get_today_kst())
    st.session_state.setdefault("counseling_date_input", st.session_state["counseling_date"])
    st.session_state.setdefault("current_audio_path", None)
    st.session_state.setdefault("selected_audio_path", None)
    st.session_state.setdefault("is_processing", False)
    st.session_state.setdefault("pending_main_run", False)
    st.session_state.setdefault("current_job_snapshot", None)
    st.session_state.setdefault("last_error", None)
    st.session_state.setdefault("last_error_details", {})
    st.session_state["draft_initialized"] = True


def sync_counseling_date_to_kst_today() -> None:
    today_kst = get_today_kst()
    today_marker = today_kst.isoformat()
    if st.session_state.get("last_kst_today") == today_marker:
        return
    st.session_state["last_kst_today"] = today_marker
    if st.session_state.get("is_processing", False):
        return
    st.session_state["counseling_date"] = today_kst
    st.session_state["counseling_date_input"] = today_kst


def clear_current_draft() -> None:
    uploaded_path = st.session_state.get("uploaded_audio_saved_path")
    if uploaded_path:
        path = Path(uploaded_path)
        try:
            if path.exists() and UPLOAD_DIR.resolve() in path.resolve().parents:
                path.unlink()
        except OSError:
            pass

    if CURRENT_DRAFT_PATH.exists():
        CURRENT_DRAFT_PATH.unlink()

    st.session_state.clear()


def iter_recording_files(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    try:
        return [
            path for path in folder.iterdir()
            if path.is_file() and path.suffix.lower().lstrip(".") in ALLOWED_EXTENSIONS
        ]
    except OSError:
        return []


def recording_created_timestamp(path: Path) -> float:
    try:
        return path.stat().st_ctime
    except OSError:
        return 0.0


def recording_created_kst_date(path: Path) -> date | None:
    try:
        return datetime.fromtimestamp(recording_created_timestamp(path), ZoneInfo("Asia/Seoul")).date()
    except OSError:
        return None


def find_today_recordings() -> list[Path]:
    today = get_today_kst()
    recordings: list[Path] = []
    for folder in recording_candidate_dirs():
        for path in iter_recording_files(folder):
            if recording_created_kst_date(path) == today:
                recordings.append(path)
    return sorted(recordings, key=recording_created_timestamp, reverse=True)


def find_latest_recording() -> Path | None:
    latest_file = None
    latest_created_time = -1.0
    for folder in recording_candidate_dirs():
        for path in iter_recording_files(folder):
            created_time = recording_created_timestamp(path)
            if created_time > latest_created_time:
                latest_file = path
                latest_created_time = created_time
    return latest_file


def format_recording_created_time(path: Path) -> str:
    return datetime.fromtimestamp(recording_created_timestamp(path)).strftime("%Y-%m-%d %H:%M:%S")


def reset_progress() -> None:
    st.session_state["progress"] = {stage: "대기" for stage in PROGRESS_STAGES}
    st.session_state["progress_detail"] = {stage: "" for stage in PROGRESS_STAGES}


def set_progress(stage: str, state: str, detail: str = "") -> None:
    if "progress" not in st.session_state:
        reset_progress()
    st.session_state["progress"][stage] = state
    st.session_state["progress_detail"][stage] = detail


def workflow_step_failure_progress_detail(stage: str, error: object) -> str:
    text = str(error or "").strip()
    if stage == "정리본 생성":
        if is_codex_authentication_error(error):
            return "Codex 로그인 필요"
        if is_codex_network_error(error):
            return "Codex 네트워크 연결 실패"
        if re.search(r"codex\s+exec\s+실행\s+실패|Codex\s+CLI\s+실행", text, flags=re.IGNORECASE):
            return "Codex CLI 실행 실패"
        return "정리본 생성 실패"
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line:
        return f"{stage} 실패"
    return first_line[:120]


def render_progress(container) -> None:
    icons = {"대기": "-", "진행 중": "…", "완료": "✓", "보완 필요": "!", "실패": "!"}
    with container:
        st.subheader("처리 상태")
        progress = st.session_state.get("progress", {stage: "대기" for stage in PROGRESS_STAGES})
        detail = st.session_state.get("progress_detail", {})
        if all(progress.get(stage, "대기") == "대기" for stage in PROGRESS_STAGES):
            st.write("- 대기 중")
            return
        for stage in PROGRESS_STAGES:
            state = progress.get(stage, "대기")
            if state == "대기":
                continue
            suffix = f" · {detail[stage]}" if detail.get(stage) else ""
            st.write(f"{icons.get(state, '-')} {stage}: {state}{suffix}")


def set_troubleshooting_info(
    stage: str,
    error: Exception | str,
    selected_audio_path: Path | None = None,
    transcript_path_value: Path | None = None,
    extra: dict[str, str | int] | None = None,
) -> None:
    info = {
        "오류 단계": stage,
        "오류 상세": str(error),
        "returncode": "-",
        "stdout": "-",
        "stderr": "-",
        "실행 명령": "-",
        "Codex 실행 경로": find_codex_executable().get("path") or "-",
        "원문 저장 경로": str(transcript_path_value or "-"),
        "업로드 파일 경로": str(selected_audio_path or "-"),
        "원문 글자 수": len(st.session_state.get("transcript_text") or st.session_state.get("rejected_transcript_text") or ""),
        "세그먼트 수": len(
            st.session_state.get("transcript_segments")
            or st.session_state.get("rejected_transcript_segments")
            or []
        ),
        "반복 의심 여부": "예" if st.session_state.get("transcript_suspicious_warning") else "아니오",
        "반복 의심 사유": st.session_state.get("transcript_suspicious_warning") or "-",
        "VAD 끄고 재시도 여부": "예" if st.session_state.get("retry_without_vad") else "아니오",
        "최종 사용한 원문 글자 수": len(st.session_state.get("transcript_text") or ""),
        "최종 사용한 세그먼트 수": len(st.session_state.get("transcript_segments") or []),
    }
    if extra:
        info.update(extra)
    st.session_state["troubleshooting_info"] = info
    st.session_state["last_error"] = stage
    st.session_state["last_error_details"] = info


def render_troubleshooting_info() -> None:
    info = st.session_state.get("troubleshooting_info")
    if not info:
        return
    with st.expander("문제 해결 정보 보기", expanded=False):
        for label, value in info.items():
            if label == "오류 상세":
                st.text_area(label, value, height=220)
            else:
                st.write(f"- {label}: `{value}`")


def audio_batch_status_label(status: str) -> str:
    return {
        "pending": "대기",
        "processing": "처리 중",
        "completed": "완료",
        "needs_review": "확인 필요",
        "failed": "실패",
    }.get(status, status)


def audio_batch_panel_expanded(state: AudioBatchState | None) -> bool:
    return bool(
        state and state["status"] == "running"
        and any(job["status"] in {"pending", "processing"} for job in state["jobs"])
    )


def render_audio_batch_admin_target(selected_job: AudioBatchJob) -> None:
    if selected_job.get("admin_saved"):
        return
    candidates = find_admin_sid_candidates_for_student(selected_job["student_name"], load_admin_api_base_url())
    if len(candidates) > 1:
        sid_options = ["", *[candidate["admin_sid"] for candidate in candidates]]
        selected_sid = st.selectbox(
            "일괄 저장 대상", options=sid_options,
            index=sid_options.index(selected_job.get("admin_sid", "")) if selected_job.get("admin_sid", "") in sid_options else 0,
            format_func=lambda sid: format_admin_sid_candidate_label(next(candidate for candidate in candidates if candidate["admin_sid"] == sid)) if sid else ADMIN_SID_MANUAL_SELECT_PLACEHOLDER,
            key=f"audio_batch_admin_sid_{selected_job['job_id']}", disabled=st.session_state.get("is_processing", False),
        )
        if selected_sid != selected_job.get("admin_sid", ""):
            state = st.session_state["audio_batch_state"]
            persist_audio_batch_session_state({**state, "jobs": [
                {**job, "admin_sid": selected_sid} if job["job_id"] == selected_job["job_id"] else job
                for job in state["jobs"]
            ]})


def render_audio_batch_state_ui(ui_locked: bool) -> None:
    if st.session_state.pop("audio_batch_recovered", False):
        st.info("중단된 일괄 처리 기록을 복구했습니다. 남은 작업 이어서 처리를 누르면 계속됩니다.")
    load_warning = st.session_state.pop("audio_batch_load_warning", "")
    if load_warning:
        st.warning(load_warning)

    state = st.session_state.get("audio_batch_state")
    if not state:
        return
    recovered = recover_stalled_audio_batch_state(
        state,
        audio_batch_now_iso(),
        runtime_scheduled=bool(st.session_state.get("pending_main_run")),
    )
    if recovered is not state:
        persist_audio_batch_session_state(recovered)
        clear_stalled_audio_batch_runtime_flags()
        state = recovered
        ui_locked = False
        st.info("멈춘 일괄 처리 기록을 복구했습니다. 남은 작업 이어서 처리를 누르면 계속됩니다.")
    counts = audio_batch_counts(state)
    saved_records = sum(bool(job.get("admin_saved")) for job in state["jobs"])
    generated_records = sum(bool(job["summary"].strip()) for job in state["jobs"])
    save_pending = sum(bool(job["summary"].strip()) and not job.get("admin_saved") for job in state["jobs"])
    st.progress(saved_records / max(state["total"], 1))
    st.caption(
        f"총 {state['total']}개 · 정리본 {generated_records}개 · ADMIN 저장 완료 {audio_batch_saved_student_count(state)}명 · "
        f"저장 대기 {save_pending}건 · 처리 중 {counts['processing']} · "
        f"대기 {counts['pending']} · 확인 필요 {counts['needs_review']} · 실패 {counts['failed']}"
    )

    processing_job = next((job for job in state["jobs"] if job["status"] == "processing"), None)
    current_status_text = {
        "completed": "일괄 저장 완료" if audio_batch_all_saved(state) else "일괄 저장 미완료",
        "paused": "일괄 처리 일시 중지",
        "running": "처리 대기 중",
    }.get(state["status"], "처리 대기 중")
    if processing_job is not None:
        position = next(
            index + 1
            for index, job in enumerate(state["jobs"])
            if job["job_id"] == processing_job["job_id"]
        )
        current_status_text = (
            f"{position}/{state['total']} 처리 중 · "
            f"{processing_job['student_name']} · {processing_job['source_name']}"
        )
    st.caption(current_status_text)

    if save_pending and st.button(
        f"5A ADMIN 일괄 적용 ({save_pending}건)",
        disabled=ui_locked or counts["pending"] > 0 or counts["processing"] > 0,
        use_container_width=True,
    ):
        st.session_state["is_processing"] = True
        try:
            with st.spinner("정리본을 학생별 ADMIN 특이사항에 저장 중입니다."):
                save_completed_audio_batch_summaries()
        finally:
            st.session_state["is_processing"] = False
        st.rerun()
    if audio_batch_all_saved(state) and not state.get("acknowledged_at"):
        if st.button("일괄 저장 완료 확인", disabled=ui_locked, use_container_width=True):
            persist_audio_batch_session_state({**state, "acknowledged_at": audio_batch_now_iso()})
            st.session_state["audio_batch_state"] = None
            st.rerun()

    action_columns = st.columns(2)
    with action_columns[0]:
        resume_disabled = ui_locked or state["status"] != "paused" or counts["pending"] == 0
        resume_button_slot = st.empty()
        if resume_button_slot.button(
            "남은 작업 이어서 처리",
            disabled=resume_disabled,
            use_container_width=True,
        ):
            resume_button_slot.info("처리를 시작했습니다.")
            resumed = resume_audio_batch_state(state, audio_batch_now_iso())
            persist_audio_batch_session_state(resumed)
            start_next_audio_batch_job_with_refresh()
    with action_columns[1]:
        problem_count = counts["failed"] + counts["needs_review"]
        retry_disabled = (
            ui_locked
            or problem_count == 0
            or state["status"] == "running"
            or counts["pending"] > 0
        )
        if st.button(
            f"문제 파일만 다시 처리 ({problem_count})",
            disabled=retry_disabled,
            use_container_width=True,
        ):
            retried = prepare_audio_batch_retry(state, audio_batch_now_iso())
            persist_audio_batch_session_state(retried)
            start_next_audio_batch_job_with_refresh()

    result_rows = []
    for index, job in enumerate(state["jobs"], start=1):
        result_rows.append(
            {
                "순서": index,
                "학생": job["student_name"],
                "파일": job["source_name"],
                "상태": "ADMIN 저장 완료" if job.get("admin_saved") else "정리본 생성 완료 · 저장 대기" if job["status"] == "completed" else audio_batch_status_label(job["status"]),
                "내용": job["detail"] or "처리 대기 중",
            }
        )
    if result_rows:
        st.dataframe(result_rows, hide_index=True, use_container_width=True)

    render_audio_batch_summary_review(state, ui_locked, AudioBatchReviewActions(
        persist=persist_audio_batch_session_state, render_target=render_audio_batch_admin_target,
        download_text=audio_batch_download_text,
    ))


def official_transcript_student_name(path: Path, counseling_date: date) -> str | None:
    if path.suffix.lower() != ".txt" or path.parent != transcript_date_dir(counseling_date):
        return None
    safe_date = safe_date_for_filename(counseling_date)
    stem = path.stem
    new_match = re.match(rf"^(.+)_{re.escape(safe_date)}_\d{{6}}_.+(?:_\d+)?$", stem)
    if new_match:
        return new_match.group(1) or None
    suffix = f"_{safe_date}"
    if stem.endswith(suffix):
        return stem[: -len(suffix)] or None
    legacy_prefix = f"{safe_date}_"
    if stem.startswith(legacy_prefix):
        return stem[len(legacy_prefix):] or None
    return None


def transcripts_for_date(counseling_date: date) -> list[Path]:
    date_dir = transcript_date_dir(counseling_date)
    if not date_dir.exists():
        return []
    visible_files = []
    for path in date_dir.glob("*.txt"):
        student_name = official_transcript_student_name(path, counseling_date)
        if not student_name:
            continue
        visible_files.append(path)

    return sorted(
        visible_files,
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def normalize_student_name_for_transcript_match(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def transcripts_for_student_date(
    counseling_date: date,
    student_name: str,
    transcripts: list[Path] | None = None,
) -> list[Path]:
    expected_name = normalize_student_name_for_transcript_match(student_name)
    if not expected_name:
        return []
    return [
        path for path in (transcripts if transcripts is not None else transcripts_for_date(counseling_date))
        if normalize_student_name_for_transcript_match(
            official_transcript_student_name(path, counseling_date)
        ) == expected_name
    ]


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", str(path)], shell=False)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], shell=False)
    else:
        subprocess.Popen(["xdg-open", str(path)], shell=False)


def main() -> None:
    st.set_page_config(page_title="5A Connect Log", layout="wide")
    apply_ui_refresh_styles()
    structure_warnings = ensure_project_structure()
    ensure_default_glossary_file()
    ensure_admin_settings_files()
    file_issues = check_required_project_files()
    initialize_draft_state()
    initialize_audio_batch_session_state()
    sync_counseling_date_to_kst_today()
    if "progress" not in st.session_state:
        reset_progress()
    st.session_state.setdefault("latest_summary", "")
    st.session_state.setdefault("transcript_text", "")
    st.session_state.setdefault("transcript_segments", [])
    st.session_state.setdefault("segment_rows", [])
    st.session_state.setdefault("transcript_suspicious_warning", "")
    st.session_state.setdefault("retry_without_vad", False)
    st.session_state.setdefault("current_audio_path", None)
    st.session_state.setdefault("selected_audio_path", None)
    st.session_state.setdefault("is_processing", False)
    st.session_state.setdefault("pending_main_run", False)
    st.session_state.setdefault("current_job_snapshot", None)
    st.session_state.setdefault("last_error", None)
    st.session_state.setdefault("last_error_details", {})
    st.session_state.setdefault("troubleshooting_info", None)
    st.session_state.setdefault("post_run_success", "")
    st.session_state.setdefault("post_run_toast", "")
    st.session_state.setdefault("post_run_warning", "")
    st.session_state.setdefault("post_run_error", "")
    st.session_state.setdefault("summary_generation_failed", False)
    st.session_state.setdefault("summary_status", "")
    st.session_state.setdefault("summary_retry_available", False)
    st.session_state.setdefault("summary_generation_diagnostics", {})
    st.session_state.setdefault("manual_summary_fallback_text", "")
    st.session_state.setdefault("audio_upload_reset_index", 0)
    st.session_state.setdefault("audio_upload_key", "audio_upload")
    st.session_state.setdefault("detected_student_name", "")
    st.session_state.setdefault("student_name_detection_message", "")
    st.session_state.setdefault("author_name", get_default_author_name())
    st.session_state.setdefault("show_developer_diagnostics", False)
    st.session_state.setdefault("codex_login_required", False)
    st.session_state.setdefault("admin_consumed_filter_enabled", True)
    st.session_state["use_academy_glossary"] = True
    st.session_state.setdefault("use_vad_filter", False)
    st.session_state.setdefault("use_prompt_hint", False)
    continue_running_audio_batch_if_needed()
    apply_pending_student_name_from_file()
    ui_locked = st.session_state.get("is_processing", False)
    app_last_modified_badge = format_app_last_modified_badge()
    show_developer_diagnostics = st.sidebar.checkbox(
        "개발자 진단 정보 표시",
        key="show_developer_diagnostics",
        disabled=ui_locked,
    )
    st.sidebar.caption(app_last_modified_badge)

    if show_developer_diagnostics:
        render_app_health_diagnostics(ui_locked)
        render_consumed_admin_notes_diagnostics(ui_locked)

    run_recording_cleanup_once_per_session()
    run_app_maintenance_cleanup_once_per_session()
    if show_developer_diagnostics and st.session_state.get("recording_cleanup_result"):
        cleanup_result = st.session_state.get("recording_cleanup_result") or {}
        with st.sidebar.expander("녹음 파일 자동 정리", expanded=False):
            st.write(f"- 마지막 정리: `{cleanup_result.get('cleaned_at', '-')}`")
            st.write(f"- 정리 기준: `{cleanup_result.get('cleanup_rule', '오늘 날짜 이전 녹음 파일')}`")
            st.write(f"- 앱 업로드 복사본: `{cleanup_result.get('uploads_moved', 0)}`개 휴지통 이동")
            st.write(f"- Windows 녹음기 원본: `{cleanup_result.get('recorder_moved', 0)}`개 휴지통 이동")
            st.write(f"- 보호된 현재 파일: `{cleanup_result.get('skipped_protected', 0)}`개")
            st.write(f"- 실패: `{cleanup_result.get('failed', 0)}`개")
            if cleanup_result.get("errors"):
                st.text_area("정리 로그", "\n".join(cleanup_result["errors"]), height=120)
    if show_developer_diagnostics and st.session_state.get("app_maintenance_cleanup_result"):
        maintenance_result = st.session_state.get("app_maintenance_cleanup_result") or {}
        with st.sidebar.expander("앱 임시 파일 자동 정리", expanded=False):
            st.write(f"- 마지막 정리: `{maintenance_result.get('cleaned_at', '-')}`")
            st.write(f"- 휴지통 이동: `{maintenance_result.get('moved', 0)}`개")
            st.write(f"- 보호된 현재 파일: `{maintenance_result.get('skipped_protected', 0)}`개")
            st.write(f"- 실패: `{maintenance_result.get('failed', 0)}`개")
            for policy in maintenance_result.get("policies", []):
                st.write(
                    f"- {policy.get('name', '-')}: "
                    f"`{policy.get('days', '-')}`일 보관 · "
                    f"`{policy.get('moved', 0)}`개 정리"
                )
            if maintenance_result.get("errors"):
                st.text_area("앱 정리 로그", "\n".join(maintenance_result["errors"]), height=120)

    st.title("5A Connect Log")
    st.caption(app_last_modified_badge)
    apply_korean_text_input_hints()
    has_blocking_startup_issue = any(issue["상태"] == "오류" for issue in file_issues)
    with st.container(key="app-feedback"):
        render_startup_warnings(structure_warnings, file_issues)
        if st.session_state.get("draft_save_error"):
            st.warning(f"임시 작업 자동 저장에 실패했습니다: {st.session_state['draft_save_error']}")
        if st.session_state.get("post_run_toast"):
            st.toast(normalize_legacy_today_task_ui_terms(st.session_state.pop("post_run_toast")))
        if st.session_state.get("post_run_success"):
            st.success(normalize_legacy_today_task_ui_terms(st.session_state.pop("post_run_success")))
        if st.session_state.get("post_run_warning"):
            st.warning(normalize_legacy_today_task_ui_terms(st.session_state.pop("post_run_warning")))
        if st.session_state.get("post_run_error"):
            st.error(normalize_legacy_today_task_ui_terms(st.session_state.pop("post_run_error")))
            render_troubleshooting_info()
        render_codex_login_recovery(ui_locked)
        if show_developer_diagnostics:
            if st.button("현재 작업 초기화", disabled=ui_locked):
                clear_current_draft()
                st.rerun()

    left_col, center_col, right_col = st.columns([0.9, 1.45, 1.0], gap="large")

    with left_col:
        st.subheader("상담 정보")
        student_name = st.text_input("학생명", placeholder="학생명 입력", key="student_name", disabled=ui_locked)
        author_name = st.text_input("작성자", key="author_name", disabled=ui_locked)
        google_docs_map = load_google_docs_map()
        current_google_doc = find_google_docs_mapping(student_name, google_docs_map)
        if student_name.strip():
            doc_url = (current_google_doc or {}).get("doc_url", "")
            if doc_url:
                st.link_button("학생 Docs 열기", doc_url)
        render_google_apps_script_url_settings_ui(ui_locked)
        counseling_date = st.date_input(
            "상담일",
            key="counseling_date_input",
            disabled=ui_locked,
        )
        st.session_state["counseling_date"] = counseling_date
        processing_mode = st.session_state.get("selected_mode")
        if processing_mode not in PROCESSING_MODES:
            processing_mode = next(iter(PROCESSING_MODES))
            st.session_state["selected_mode"] = processing_mode
        summary_style = st.session_state.get("selected_summary_style")
        if summary_style not in SUMMARY_STYLES:
            summary_style = next(iter(SUMMARY_STYLES))
            st.session_state["selected_summary_style"] = summary_style
        if show_developer_diagnostics:
            with st.expander("처리/정리본 고급 설정", expanded=False):
                processing_mode = st.selectbox(
                    "처리 모드",
                    list(PROCESSING_MODES.keys()),
                    key="selected_mode",
                    disabled=ui_locked,
                )
                summary_style = st.selectbox(
                    "정리본 스타일",
                    list(SUMMARY_STYLES.keys()),
                    key="selected_summary_style",
                    disabled=ui_locked,
                )
        mode_config = PROCESSING_MODES[processing_mode]
        model_size = mode_config["model_size"]
        beam_size = mode_config["beam_size"]
        compute_type = mode_config["compute_type"]
        render_google_docs_mapping_add_ui(ui_locked)
        use_academy_glossary = True
        use_vad_filter = bool(st.session_state.get("use_vad_filter", False))
        use_prompt_hint = bool(st.session_state.get("use_prompt_hint", False))
        if show_developer_diagnostics:
            with st.expander("Google Docs 매핑", expanded=False):
                st.write(f"- 매핑 파일 경로: `{GOOGLE_DOCS_MAP_PATH}`")
                st.write(f"- 등록된 학생 수: `{len(google_docs_map):,}`")
                st.write(f"- 현재 학생명: `{student_name.strip() or '-'}`")
                map_mtime = google_docs_map_mtime()
                if map_mtime:
                    st.write(
                        "- CSV 마지막 수정 시간: "
                        f"`{datetime.fromtimestamp(map_mtime).strftime('%Y-%m-%d %H:%M:%S')}`"
                    )
                else:
                    st.write("- CSV 마지막 수정 시간: `-`")
                if st.button("매핑 다시 읽기", disabled=ui_locked):
                    load_google_docs_map_cached.clear()
                    load_google_docs_map_rows_cached.clear()
                    load_admin_sid_map_cached.clear()
                    st.rerun()
                if current_google_doc:
                    doc_id = current_google_doc.get("doc_id") or "-"
                    doc_url = current_google_doc.get("doc_url") or ""
                    st.write(f"- 매칭된 doc_id: `{doc_id}`")
                    st.write(f"- 매칭된 doc_url: `{doc_url or '-'}`")
                    if doc_url and not ui_locked:
                        st.link_button("학생 Google Docs 열기", doc_url)
                mapping_issues = analyze_student_mapping_health()
                st.divider()
                st.write(f"- admin SID 연결 정보 수: `{len(load_admin_sid_map()):,}`")
                st.write(f"- 매핑 점검 이슈: `{len(mapping_issues):,}`개")
                if mapping_issues:
                    st.dataframe(mapping_issues[:100], use_container_width=True, hide_index=True)
                else:
                    st.success("학생 매핑 점검 결과 문제가 없습니다.")

            glossary_terms = load_academy_glossary()
            with st.expander("학원 용어집", expanded=False):
                st.write("- 정리본에 학원 용어집 반영: `항상 켜짐`")
                st.write(f"- 용어집 파일 경로: `{GLOSSARY_PATH}`")
                st.write(f"- 등록된 용어 개수: `{len(glossary_terms):,}`")
                if glossary_terms:
                    preview_terms = glossary_terms[:5]
                    st.dataframe(
                        [
                            {
                                "category": item.get("category", ""),
                                "term": item.get("term", ""),
                                "aliases": "; ".join(item.get("aliases", [])) if isinstance(item.get("aliases"), list) else "",
                                "note": item.get("note", ""),
                            }
                            for item in preview_terms
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
                if st.button("용어집 폴더 열기", disabled=ui_locked):
                    try:
                        open_folder(GLOSSARY_DIR)
                    except Exception as exc:
                        st.error(f"용어집 폴더 열기에 실패했습니다: {exc}")
            with st.expander("고급 음성 인식 설정", expanded=False):
                use_vad_filter = st.checkbox(
                    "무음 구간 자동 제거 사용",
                    key="use_vad_filter",
                    disabled=ui_locked,
                )
                use_prompt_hint = st.checkbox(
                    "음성 인식 힌트 사용",
                    key="use_prompt_hint",
                    disabled=ui_locked,
                )

        if show_developer_diagnostics:
            with st.expander("현재 음성 인식 설정", expanded=False):
                st.write(f"- 모드: {processing_mode}")
                st.write(f"- 모델: {model_size}")
                st.write(f"- beam_size: {beam_size}")
                st.write(f"- compute_type: {compute_type}")
                st.write("- 언어: ko")
                st.write(f"- VAD: {'켜짐' if use_vad_filter else '꺼짐'}")
                st.write(f"- 음성 인식 힌트: {'켜짐' if use_prompt_hint else '꺼짐'}")
                st.write(f"- 실제 initial_prompt 전달 여부: {'예' if use_prompt_hint else '아니오'}")
                st.write("- condition_on_previous_text: False")
                st.write("- 모델 캐시: st.cache_resource 사용 중")

    target_path = None
    if student_name.strip():
        target_path = transcript_date_dir(counseling_date)
        form_key = (
            f"{counseling_date.isoformat()}|"
            f"{sanitize_filename_part(student_name)}|"
            f"{sanitize_filename_part(author_name)}"
        )
        if st.session_state.get("form_transcript_key") != form_key:
            st.session_state["form_transcript_key"] = form_key
            st.session_state["current_transcript_path"] = ""
            st.session_state["latest_summary"] = ""
            st.session_state["summary_generation_failed"] = False
            st.session_state["summary_status"] = ""
            st.session_state["summary_retry_available"] = False
            st.session_state["summary_generation_diagnostics"] = {}
    save_draft(st.session_state.get("last_step", "입력값 수정"))

    with center_col:
        audio_upload_placeholder, audio_action_placeholder, history_summary_placeholder = create_audio_log_overview()
        audio_details_container = st.container(key="audio-details")
        log_csv_container = st.container(key="log-csv-workspace")
    with right_col:
        status_placeholder = st.empty()
    latest_processing_history = load_current_log_csv_processing_history()
    if latest_processing_history is not None:
        history_summary_placeholder = refresh_log_csv_processing_history_summary(
            history_summary_placeholder,
            latest_processing_history,
        )
    render_progress(status_placeholder.container())

    with right_col:
        st.divider()
        st.subheader("오\u2060늘 정\u2060리\u2060본 생\u2060성\u00a0기\u2060록")
        transcript_folder = transcript_date_dir(counseling_date)
        if show_developer_diagnostics:
            if st.button("정리본 기록 폴더 열기", disabled=ui_locked):
                try:
                    open_folder(transcript_folder)
                except Exception as exc:
                    st.error(f"정리본 기록 폴더를 열 수 없습니다: {exc}")

        all_date_transcripts = transcripts_for_date(counseling_date)
        current_transcript_path_value = st.session_state.get("current_transcript_path") or ""
        current_transcript_resolved: Path | None = None
        if current_transcript_path_value:
            try:
                current_transcript_resolved = Path(current_transcript_path_value).resolve()
            except OSError:
                current_transcript_resolved = None
        current_student_key = normalize_student_name_for_transcript_match(student_name)
        if all_date_transcripts:
            for transcript_path in all_date_transcripts:
                transcript_student_name = official_transcript_student_name(transcript_path, counseling_date) or ""
                markers: list[str] = []
                if (
                    current_student_key
                    and normalize_student_name_for_transcript_match(transcript_student_name) == current_student_key
                ):
                    markers.append("현재 학생")
                if current_transcript_resolved is not None:
                    try:
                        if transcript_path.resolve() == current_transcript_resolved:
                            markers.append("현재 사용 중")
                    except OSError:
                        pass
                marker_text = f" · {' · '.join(markers)}" if markers else ""
                display_name = transcript_student_name or transcript_path.stem
                st.markdown(f"- `{display_name}` · `{transcript_path.name}`{marker_text}")
        else:
            st.caption("오늘 정리본 생성 기록이 없습니다.")

        if show_developer_diagnostics:
            current_student_transcripts = transcripts_for_student_date(
                counseling_date,
                student_name,
                transcripts=all_date_transcripts,
            )
            with st.expander("현재 학생 정리본 생성 기록", expanded=False):
                if current_student_transcripts:
                    for transcript_path in current_student_transcripts:
                        st.markdown(f"- `{transcript_path.name}`")
                elif student_name.strip():
                    st.caption("현재 학생의 정리본 생성 기록이 없습니다.")
                else:
                    st.caption("학생명을 입력하면 현재 학생의 정리본 생성 기록만 표시됩니다.")

    def update_progress(stage: str, state: str, detail: str = "") -> None:
        set_progress(stage, state, detail)
        if state in {"진행 중", "완료", "실패"}:
            save_draft(stage)
        render_progress(status_placeholder.container())

    with audio_details_container:
        batch_state = st.session_state.get("audio_batch_state")
        batch_counts = audio_batch_counts(batch_state) if batch_state else None
        unfinished_batch = bool(
            batch_counts
            and (batch_counts["pending"] > 0 or batch_counts["processing"] > 0)
        )
        uploaded_files = audio_upload_placeholder.file_uploader(
            f"상담 녹음 파일 업로드 · 최대 {AUDIO_BATCH_MAX_FILES}개",
            label_visibility="collapsed",
            type=sorted(ALLOWED_EXTENSIONS),
            accept_multiple_files=True,
            disabled=ui_locked or unfinished_batch,
            key=st.session_state.get("audio_upload_key", "audio_upload"),
            on_change=handle_audio_upload_change,
        )
        upload_mode = classify_audio_upload_selection(len(uploaded_files))
        uploaded_file = uploaded_files[0] if upload_mode == "single" else None
        if uploaded_file is not None:
            try:
                save_uploaded_file_copy(uploaded_file)
                save_draft("녹음 파일 업로드")
            except Exception as exc:
                st.error(f"업로드 파일 보관에 실패했습니다: {exc}")

        batch_expanded = audio_batch_panel_expanded(batch_state)
        if batch_state:
            with st.expander("일괄 처리 현황", expanded=batch_expanded):
                render_audio_batch_state_ui(ui_locked)
        else:
            render_audio_batch_state_ui(ui_locked)

        if unfinished_batch and not ui_locked:
            st.caption("진행 중인 일괄 작업을 먼저 이어서 완료한 뒤 새 파일을 선택하세요.")

        batch_uploaded_files = uploaded_files if upload_mode == "batch" else []
        if upload_mode == "over_limit":
            st.error(
                f"한 번에 최대 {AUDIO_BATCH_MAX_FILES}개까지 처리할 수 있습니다. "
                f"현재 {len(uploaded_files)}개가 선택되었습니다."
            )
        elif batch_uploaded_files:
            st.caption("학생명을 확인하세요.")
            batch_student_names: list[str] = []
            name_columns = st.columns(2)
            for index, batch_file in enumerate(batch_uploaded_files):
                detected_name = extract_student_name_from_audio_filename(batch_file.name) or ""
                signature = hashlib.sha256(
                    f"{index}|{batch_file.name}|{batch_file.size}".encode("utf-8")
                ).hexdigest()[:16]
                input_key = f"audio_batch_student_{signature}"
                st.session_state.setdefault(input_key, detected_name)
                with name_columns[index % 2]:
                    st.caption(batch_file.name)
                    batch_student_names.append(
                        st.text_input(
                            "학생명",
                            key=input_key,
                            disabled=ui_locked,
                            label_visibility="collapsed",
                            placeholder="학생명 입력",
                        ).strip()
                    )

            missing_name_count = sum(not name for name in batch_student_names)
            if missing_name_count:
                st.warning(f"학생명이 비어 있는 파일이 {missing_name_count}개 있습니다.")
            start_batch_disabled = ui_locked or bool(missing_name_count) or has_blocking_startup_issue
            if st.button(
                f"선택한 녹음 {len(batch_uploaded_files)}개 순차 처리",
                disabled=start_batch_disabled,
                type="primary",
                use_container_width=True,
            ):
                try:
                    if not prepare_codex_summary_processing():
                        st.rerun()
                    jobs: list[AudioBatchJob] = []
                    for batch_file, batch_student_name in zip(
                        batch_uploaded_files,
                        batch_student_names,
                        strict=True,
                    ):
                        saved_path = save_audio_batch_upload_copy(batch_file)
                        jobs.append(
                            create_audio_batch_job(
                                audio_path=str(saved_path),
                                source_name=batch_file.name,
                                student_name=batch_student_name,
                                author_name=author_name.strip() or get_default_author_name(),
                                counseling_date=counseling_date.isoformat(),
                                processing_mode=processing_mode,
                                summary_style=summary_style,
                                use_vad=use_vad_filter,
                                use_prompt_hint=use_prompt_hint,
                            )
                        )
                    new_state = create_audio_batch_state(jobs, audio_batch_now_iso())
                    persist_audio_batch_session_state(new_state)
                    st.session_state.pop("audio_batch_summary_choice", None)
                    reset_index = st.session_state.get("audio_upload_reset_index", 0) + 1
                    st.session_state["audio_upload_reset_index"] = reset_index
                    st.session_state["audio_upload_key"] = f"audio_upload_{reset_index}"
                    start_next_audio_batch_job_with_refresh()
                except (OSError, ValueError, RuntimeError) as exc:
                    st.error(f"일괄 처리 준비에 실패했습니다: {exc}")

        if show_developer_diagnostics:
            with st.expander("보조 기능", expanded=False):
                if st.button("최근 녹음 불러오기", disabled=ui_locked):
                    latest_recording = find_latest_recording()
                    if latest_recording:
                        st.session_state["recent_recording_path"] = str(latest_recording)
                        st.session_state["uploaded_audio_saved_path"] = str(latest_recording)
                        st.session_state["current_audio_path"] = str(latest_recording)
                        queue_student_name_from_audio_filename(latest_recording)
                        save_draft("최근 녹음 불러오기")
                        st.success("최근 녹음 파일을 찾았습니다.")
                        st.rerun()
                    else:
                        st.session_state.pop("recent_recording_path", None)
                        st.session_state["detected_student_name"] = ""
                        st.warning("후보 폴더에서 최근 녹음 파일을 찾지 못했습니다.")

                today_recordings = find_today_recordings()
                if today_recordings:
                    today_options = {
                        f"{path.name} · {format_recording_created_time(path)}": str(path)
                        for path in today_recordings
                    }
                    selected_today_recording = st.selectbox(
                        "오늘 녹음 파일",
                        options=list(today_options.keys()),
                        disabled=ui_locked,
                        key="today_recording_choice",
                    )
                    selected_path = Path(today_options[selected_today_recording])
                    selected_today_path = str(selected_path)
                    if (
                        not ui_locked
                        and st.session_state.get("selected_today_recording_path") != selected_today_path
                    ):
                        st.session_state["selected_today_recording_path"] = selected_today_path
                        st.session_state["recent_recording_path"] = str(selected_path)
                        st.session_state["uploaded_audio_saved_path"] = str(selected_path)
                        st.session_state["current_audio_path"] = str(selected_path)
                        queue_student_name_from_audio_filename(selected_path)
                        save_draft("오늘 녹음 파일 선택")
                        st.rerun()

        recent_path = None
        if st.session_state.get("recent_recording_path"):
            recent_path = Path(st.session_state["recent_recording_path"])
            if not recent_path.exists():
                st.session_state.pop("recent_recording_path", None)
                recent_path = None

        selected_audio_label = None
        if upload_mode in {"batch", "over_limit"}:
            selected_audio_path = None
            selected_audio_label = None
        elif uploaded_file is not None and st.session_state.get("current_audio_path"):
            selected_audio_path = Path(st.session_state["current_audio_path"])
            selected_audio_label = selected_audio_path.name
        elif recent_path is not None:
            selected_audio_path = recent_path
            selected_audio_label = recent_path.name
        elif st.session_state.get("current_audio_path") and Path(
            st.session_state["current_audio_path"]
        ).exists():
            selected_audio_path = Path(st.session_state["current_audio_path"])
            selected_audio_label = selected_audio_path.name
        else:
            selected_audio_path = None

        st.session_state["selected_audio_path"] = str(selected_audio_path) if selected_audio_path else None
        detected_student_name = extract_student_name_from_audio_filename(selected_audio_path) if selected_audio_path else None
        if detected_student_name:
            st.session_state["detected_student_name"] = detected_student_name
        elif selected_audio_path:
            st.session_state["student_name_detection_message"] = "파일명에서 학생명을 찾지 못했습니다. 직접 입력하세요."

        if selected_audio_path and is_m4a_or_mp3(selected_audio_path) and not has_ffmpeg():
            st.warning(ffmpeg_missing_audio_warning())

        can_run = bool(student_name.strip()) and selected_audio_label is not None and not has_blocking_startup_issue
        is_processing = st.session_state.get("is_processing", False)
        button_disabled = (not can_run) or is_processing

        def start_single_consultation_job() -> None:
            if not prepare_codex_summary_processing():
                return
            st.session_state["is_processing"] = True
            st.session_state["pending_main_run"] = True
            st.session_state["latest_summary"] = ""
            st.session_state["summary_generation_failed"] = False
            st.session_state["summary_status"] = ""
            st.session_state["summary_retry_available"] = False
            st.session_state["summary_generation_diagnostics"] = {}
            st.session_state["post_run_success"] = ""
            st.session_state["post_run_toast"] = ""
            st.session_state["post_run_warning"] = ""
            st.session_state["post_run_error"] = ""
            st.session_state["current_job_snapshot"] = {
                "student_name": student_name.strip(),
                "author_name": author_name.strip() or get_default_author_name(),
                "counseling_date": counseling_date.isoformat(),
                "audio_path": str(selected_audio_path),
                "processing_mode": processing_mode,
                "summary_style": summary_style,
                "use_academy_glossary": True,
                "use_vad": use_vad_filter,
                "use_prompt_hint": use_prompt_hint,
            }

        if upload_mode in {"empty", "single"}:
            audio_action_placeholder.button("정리본 생성·복사",
                disabled=button_disabled,
                on_click=start_single_consultation_job,
            )

        if st.session_state.get("pending_main_run"):
            st.session_state["pending_main_run"] = False
            snapshot = st.session_state.get("current_job_snapshot") or {}
            student_name = snapshot.get("student_name", student_name).strip()
            try:
                counseling_date = date.fromisoformat(str(snapshot.get("counseling_date")))
            except (TypeError, ValueError):
                counseling_date = st.session_state.get("counseling_date", get_today_kst())
            selected_audio_path = Path(snapshot["audio_path"]) if snapshot.get("audio_path") else None
            selected_audio_label = selected_audio_path.name if selected_audio_path else None
            processing_mode = snapshot.get("processing_mode", processing_mode)
            if processing_mode not in PROCESSING_MODES:
                processing_mode = "균형 모드"
            mode_config = PROCESSING_MODES[processing_mode]
            model_size = mode_config["model_size"]
            beam_size = mode_config["beam_size"]
            compute_type = mode_config["compute_type"]
            summary_style = snapshot.get("summary_style", summary_style)
            if summary_style not in SUMMARY_STYLES:
                summary_style = "기본 상담 정리"
            author_name = snapshot.get("author_name", author_name).strip() or get_default_author_name()
            use_academy_glossary = True
            use_vad_filter = bool(snapshot.get("use_vad", use_vad_filter))
            use_prompt_hint = bool(snapshot.get("use_prompt_hint", use_prompt_hint))
            target_path = transcript_date_dir(counseling_date) if student_name else None
            batch_job_id = str(snapshot.get("batch_job_id") or "")
            batch_item_status = "failed"
            batch_item_detail = "처리 중 오류"
            batch_item_summary = ""
            batch_item_transcript_path = str(snapshot.get("resume_transcript_path") or "")
            pause_audio_batch = False
            st.session_state["troubleshooting_info"] = None
            reset_progress()
            render_progress(status_placeholder.container())

            if batch_job_id and snapshot.get("resume_admin_save_only"):
                batch_job = next(job for job in st.session_state["audio_batch_state"]["jobs"] if job["job_id"] == batch_job_id)
                st.session_state["latest_summary"] = batch_job["summary"]
                st.session_state["current_transcript_path"] = batch_job["transcript_path"]
                finish_active_audio_batch_job(
                    status="completed", detail="ADMIN 저장 재시도", transcript_path=batch_job["transcript_path"],
                    summary=batch_job["summary"], pause_batch=False,
                )
                save_audio_batch_job_to_admin(batch_job_id)
                save_draft("ADMIN 저장")
                st.rerun()

            if batch_job_id and snapshot.get("resume_summary_only"):
                resume_transcript_path = Path(batch_item_transcript_path)
                try:
                    resume_transcript = read_saved_transcript_body(resume_transcript_path)
                    if not resume_transcript.strip():
                        raise WorkflowStepError(
                            "원문 저장",
                            "이어 처리할 저장 원문을 읽지 못했습니다.",
                        )
                    st.session_state["current_transcript_path"] = str(resume_transcript_path)
                    st.session_state["transcript_text"] = resume_transcript
                    st.session_state["transcript_segments"] = []
                    update_progress("녹음 파일 확인", "완료", "저장된 원문 사용")
                    update_progress("저장 파일 준비", "완료", "기존 원문 경로 확인")
                    update_progress("모델 로딩", "완료", "재추출 생략")
                    update_progress("원문 추출", "완료", f"저장된 원문 {len(resume_transcript):,}자")
                    update_progress("원문 저장", "완료", "기존 원문 유지")
                    update_progress("정리본 생성", "진행 중", "저장된 원문으로 이어서 생성")
                    summary_result = generate_summary_from_transcript(
                        student_name,
                        counseling_date,
                        resume_transcript,
                        summary_style,
                        use_academy_glossary,
                        max_invalid_retries=1,
                        source_transcript_path=resume_transcript_path,
                        retry_context="audio_batch_resume",
                        transcript_source="saved_transcript_batch_resume",
                        on_retry=lambda reason: update_progress(
                            "정리본 생성",
                            "진행 중",
                            summary_retry_progress_message(reason),
                        ),
                    )
                    summary = str(summary_result["summary"])
                    st.session_state["latest_summary"] = summary
                    st.session_state["summary_generation_failed"] = False
                    st.session_state["summary_status"] = SUMMARY_STATUS_READY
                    st.session_state["summary_retry_available"] = False
                    st.session_state["summary_generation_diagnostics"] = summary_result.get("diagnostics", {})
                    update_progress("정리본 생성", "완료", summary_style)
                    copied, copy_error = copy_to_clipboard(summary)
                    if copied:
                        update_progress("클립보드 복사", "완료", "현재 정리본 복사 완료")
                        batch_item_detail = "정리본 생성 완료"
                    else:
                        update_progress("클립보드 복사", "실패", copy_error or "")
                        batch_item_detail = "정리본 생성 완료 · 클립보드 확인 필요"
                    batch_item_status = "completed"
                    batch_item_summary = summary
                except WorkflowStepError as exc:
                    update_progress(exc.stage, "실패", workflow_step_failure_progress_detail(exc.stage, exc))
                    batch_item_status = "pending" if should_pause_audio_batch_for_codex_error(exc) else "needs_review"
                    batch_item_detail = str(exc).splitlines()[0][:160]
                    pause_audio_batch = should_pause_audio_batch_for_codex_error(exc)
                    clear_failed_summary_state()
                    set_troubleshooting_info(exc.stage, exc, selected_audio_path, resume_transcript_path)
                except RuntimeError as exc:
                    update_progress("정리본 생성", "실패", workflow_step_failure_progress_detail("정리본 생성", exc))
                    batch_item_status = "pending" if should_pause_audio_batch_for_codex_error(exc) else "needs_review"
                    batch_item_detail = str(exc).splitlines()[0][:160]
                    pause_audio_batch = should_pause_audio_batch_for_codex_error(exc)
                    clear_failed_summary_state()
                    set_troubleshooting_info("정리본 생성", exc, selected_audio_path, resume_transcript_path)
                finally:
                    finish_active_audio_batch_job(
                        status=batch_item_status,
                        detail=batch_item_detail,
                        transcript_path=batch_item_transcript_path,
                        summary=batch_item_summary,
                        pause_batch=pause_audio_batch,
                    )
                    save_draft(st.session_state.get("last_step", "정리본 생성"))
                    st.rerun()

            transcript_file: Path | None = None
            try:
                update_progress("녹음 파일 확인", "진행 중", selected_audio_label)
                if selected_audio_path is None or not selected_audio_path.exists():
                    raise WorkflowStepError("녹음 파일 확인", "녹음 파일을 먼저 선택하세요.")
                audio_path = selected_audio_path
                update_progress("녹음 파일 확인", "완료", f"{Path(audio_path).name} 확인 완료")

                st.session_state["transcript_text"] = ""
                st.session_state["transcript_segments"] = []
                st.session_state["rejected_transcript_text"] = ""
                st.session_state["rejected_transcript_segments"] = []
                st.session_state["last_transcript_suspicion"] = ""
                st.session_state["transcript_suspicious_warning"] = ""
                st.session_state["retry_without_vad"] = False
                st.session_state["last_transcript_initial_prompt_used"] = use_prompt_hint
                st.session_state["last_transcript_condition_on_previous_text"] = False
                st.session_state["summary_status"] = ""
                st.session_state["summary_generation_diagnostics"] = {}

                model_status_placeholder = st.empty()
                extraction_status_placeholder = st.empty()
                extraction_progress_bar = st.empty()

                model_started_at = time.monotonic()
                update_progress("모델 로딩", "진행 중", "모델 로딩 중")
                model_status_placeholder.caption("모델 로딩 중...")
                try:
                    model = load_whisper_model(model_size, compute_type)
                except Exception as exc:
                    raise WorkflowStepError("원문 추출", str(exc)) from exc
                model_elapsed = time.monotonic() - model_started_at
                model_status_placeholder.caption(f"모델 로딩 완료 · {format_elapsed(model_elapsed)}")
                update_progress("모델 로딩", "완료", f"{format_elapsed(model_elapsed)}")

                extraction_started_at = time.monotonic()
                progress_bar = None
                extraction_status_placeholder.caption("원문 추출 중...")
                update_progress("원문 추출", "진행 중", "00분 00초")

                def update_transcription_progress(progress_info: dict) -> None:
                    nonlocal progress_bar
                    elapsed_text = format_elapsed(progress_info["elapsed"])
                    segment_count = progress_info["segment_count"]
                    char_count = progress_info["char_count"]
                    audio_duration = progress_info.get("audio_duration")
                    progress = progress_info.get("progress")
                    duration_text = format_elapsed(audio_duration) if audio_duration else "알 수 없음"
                    if progress is not None:
                        if progress_bar is None:
                            progress_bar = extraction_progress_bar.progress(0)
                        percent = int(progress * 100)
                        progress_bar.progress(percent)
                        extraction_status_placeholder.caption(
                            f"원문 추출 중 · {elapsed_text} 경과 · 오디오 {duration_text} · "
                            f"{percent}% 정도 · {segment_count}개 구간 · {char_count}자"
                        )
                        update_progress("원문 추출", "진행 중", f"{elapsed_text} · {percent}%")
                    else:
                        extraction_status_placeholder.caption(
                            f"원문 추출 중 · {elapsed_text} 경과 · {segment_count}개 구간 · {char_count}자"
                        )
                        update_progress("원문 추출", "진행 중", elapsed_text)

                try:
                    transcript, segment_rows = transcribe_audio(
                        audio_path,
                        model,
                        beam_size,
                        use_vad_filter,
                        student_name,
                        use_prompt_hint,
                        processing_mode,
                        update_transcription_progress,
                    )
                    segment_rows = segment_rows or []
                except Exception as exc:
                    elapsed_text = format_elapsed(time.monotonic() - extraction_started_at)
                    extraction_status_placeholder.caption(f"원문 추출 실패 · {elapsed_text} 경과")
                    ffmpeg_hint = ""
                    if is_m4a_or_mp3(audio_path) and not has_ffmpeg():
                        ffmpeg_hint = " m4a/mp3 파일 처리에는 ffmpeg가 필요할 수 있습니다. ffmpeg 설치 후 앱을 재시작해보세요."
                    extract_debug = st.session_state.get("last_extract_debug_info") or {}
                    actual_type = extract_debug.get("exception_type") or type(exc).__name__
                    actual_message = extract_debug.get("exception_message") or str(exc)
                    debug_log_path = extract_debug.get("debug_log_path") or "-"
                    converted_wav_path = extract_debug.get("converted_wav_path") or "-"
                    retry_used = extract_debug.get("retry_used", False)
                    ffmpeg_hint += (
                        f"\nexception type: {actual_type}"
                        f"\nexception message: {actual_message}"
                        f"\nconverted wav path: {converted_wav_path}"
                        f"\nvad_filter=False retry: {'yes' if retry_used else 'no'}"
                        f"\ndebug log file: {debug_log_path}"
                    )
                    raise WorkflowStepError(
                        "원문 추출",
                        f"원문 추출에 실패했습니다. 경과 시간: {elapsed_text}. "
                        f"녹음 파일을 확인한 뒤 다시 실행하세요.{ffmpeg_hint}",
                    ) from exc
                extraction_elapsed = time.monotonic() - extraction_started_at
                if progress_bar is not None:
                    progress_bar.progress(100)
                safe_transcript = transcript or ""
                safe_segment_rows = segment_rows or []
                extraction_status_placeholder.caption(
                    f"원문 추출 완료 · 소요 시간 {format_elapsed(extraction_elapsed)} · "
                    f"세그먼트 {len(safe_segment_rows)}개 · {len(safe_transcript):,}자"
                )
                if not safe_transcript.strip() or len(safe_transcript.strip()) < 30 or len(safe_segment_rows) == 0:
                    st.session_state["rejected_transcript_text"] = safe_transcript
                    st.session_state["rejected_transcript_segments"] = segment_rows
                    st.session_state["last_transcript_suspicion"] = "원문 추출 결과가 비어 있거나 너무 짧습니다."
                    update_progress("원문 추출", "실패", f"{len(safe_transcript.strip())}자")
                    raise WorkflowStepError(
                        "원문 추출",
                        "원문 추출 결과가 비어 있거나 너무 짧습니다. 녹음 파일과 인식 설정을 확인하세요.",
                    )
                suspicion_reason = detect_suspicious_transcript(safe_transcript)
                if suspicion_reason:
                    retry_without_vad = False
                    retry_suspicion_reason = ""
                    if use_vad_filter or use_prompt_hint:
                        try:
                            retry_transcript, retry_segment_rows = transcribe_audio(
                                audio_path,
                                model,
                                beam_size,
                                False,
                                student_name,
                                False,
                                processing_mode,
                                update_transcription_progress,
                            )
                            retry_without_vad = True
                            retry_transcript = retry_transcript or ""
                            retry_segment_rows = retry_segment_rows or []
                            retry_suspicion_reason = detect_suspicious_transcript(retry_transcript)
                            retry_is_usable = (
                                retry_transcript.strip()
                                and len(retry_transcript.strip()) >= 30
                                and len(retry_segment_rows) > 0
                            )
                            if retry_is_usable and (
                                not retry_suspicion_reason
                                or len(retry_transcript) > len(safe_transcript)
                            ):
                                safe_transcript = retry_transcript
                                safe_segment_rows = retry_segment_rows
                                suspicion_reason = retry_suspicion_reason
                        except Exception as retry_exc:
                            retry_without_vad = True
                            retry_suspicion_reason = f"VAD 끄고 재시도 실패: {type(retry_exc).__name__}: {retry_exc}"

                    warning_reason = (
                        suspicion_reason
                        or retry_suspicion_reason
                        or "1차 반복 의심 후 VAD/힌트 끄고 재시도 결과를 사용했습니다."
                    )
                    st.session_state["last_transcript_suspicion"] = warning_reason
                    st.session_state["transcript_suspicious_warning"] = warning_reason
                    st.session_state["retry_without_vad"] = retry_without_vad
                    update_progress("원문 추출", "완료", "반복 의심 경고")
                st.session_state["transcript_text"] = safe_transcript
                st.session_state["transcript_segments"] = safe_segment_rows
                if not st.session_state.get("transcript_suspicious_warning"):
                    st.session_state["last_transcript_suspicion"] = ""
                    st.session_state["transcript_suspicious_warning"] = ""
                    st.session_state["retry_without_vad"] = False
                save_draft("원문 추출")
                update_progress(
                    "원문 추출",
                    "완료",
                    f"{format_elapsed(extraction_elapsed)} · {len(safe_segment_rows)}개 · {len(safe_transcript):,}자",
                )
                if len(safe_transcript) < 50:
                    st.warning("원문이 짧게 추출되었습니다. 정확 모드로 변경하고, VAD를 끈 상태에서 다시 시도해보세요.")

                update_progress("저장 파일 준비", "완료", "새 원문 파일명 생성")

                update_progress("원문 저장", "진행 중")
                try:
                    transcript_file = write_transcript_file_unique(
                        counseling_date,
                        student_name,
                        author_name,
                        format_transcript_file(student_name, counseling_date, safe_transcript),
                    )
                except Exception as exc:
                    raise WorkflowStepError("원문 저장", str(exc)) from exc
                target_path = transcript_file
                st.session_state["current_transcript_path"] = str(transcript_file)
                if batch_job_id:
                    checkpoint_active_audio_batch_transcript(transcript_file)
                    batch_item_transcript_path = str(transcript_file)
                save_draft("원문 저장")
                saved_transcript_display_path = transcript_file.relative_to(BASE_DIR)
                update_progress("원문 저장", "완료", f"원문 자동 저장 완료: {saved_transcript_display_path}")
                sufficiency_diagnostics = transcript_sufficiency_diagnostics(safe_transcript)
                if sufficiency_diagnostics["insufficient_transcript"]:
                    clear_insufficient_transcript_summary_state(sufficiency_diagnostics)
                    update_progress(
                        "정리본 생성",
                        "보완 필요",
                        (
                            "원문 부족으로 보완 필요 · "
                            f"{sufficiency_diagnostics['transcript_length_chars']}자"
                        ),
                    )
                    raise InsufficientTranscriptError(
                        "정리본 생성",
                        "원문이 너무 짧아 자동 정리본을 만들 근거가 부족합니다.",
                    )

                update_progress("정리본 생성", "진행 중", "정리본 생성 중")
                suspicion_reason = detect_suspicious_transcript(safe_transcript)
                if suspicion_reason:
                    st.session_state["last_transcript_suspicion"] = suspicion_reason
                    st.session_state["transcript_suspicious_warning"] = suspicion_reason
                try:
                    summary_result = generate_summary_from_transcript(
                        student_name,
                        counseling_date,
                        safe_transcript,
                        summary_style,
                        use_academy_glossary,
                        max_invalid_retries=1,
                        source_transcript_path=transcript_file,
                        retry_context="initial_run",
                        transcript_source="current_run_transcript",
                        on_retry=lambda reason: update_progress(
                            "정리본 생성",
                            "진행 중",
                            summary_retry_progress_message(reason),
                        ),
                    )
                except WorkflowStepError:
                    raise
                except Exception as exc:
                    raise WorkflowStepError("정리본 생성", str(exc)) from exc
                summary = str(summary_result["summary"])
                prompt = str(summary_result["prompt"])
                st.session_state["latest_summary"] = summary
                st.session_state["summary_generation_failed"] = False
                st.session_state["summary_status"] = SUMMARY_STATUS_READY
                st.session_state["summary_retry_available"] = False
                st.session_state["summary_generation_diagnostics"] = summary_result.get("diagnostics", {})
                update_progress("정리본 생성", "완료", summary_style)
                if batch_job_id:
                    batch_item_status = "completed"
                    batch_item_detail = "정리본 생성 완료"
                    batch_item_summary = summary
                    batch_item_transcript_path = str(transcript_file)

                update_progress("클립보드 복사", "진행 중")
                clipboard_text = summary
                copied, copy_error = copy_to_clipboard(clipboard_text)
                if copied:
                    update_progress("클립보드 복사", "완료", "정리본 전체 복사 완료")
                    if batch_job_id:
                        batch_item_detail = "정리본 생성 완료"
                    else:
                        st.session_state["post_run_toast"] = (
                            f"원문 자동 저장 완료:\n{saved_transcript_display_path}\n\n"
                            "정리본 전체가 클립보드에 복사되었습니다. Ctrl+V로 붙여넣으세요."
                        )
                        st.session_state["post_run_success"] = ""
                        clear_selected_audio_after_success()
                else:
                    update_progress("클립보드 복사", "실패", copy_error or "")
                    if batch_job_id:
                        batch_item_detail = "정리본 생성 완료 · 클립보드 확인 필요"
                    else:
                        st.session_state["post_run_warning"] = "클립보드 복사 단계에서 실패했습니다. 정리본 확인 및 수정 영역에서 정리본 전체를 직접 복사하세요."
                        st.warning("클립보드 복사 단계에서 실패했습니다. 아래 내용을 직접 복사하세요.")
                        if copy_error:
                            st.caption(copy_error)
                        st.text_area("복사용 정리본", clipboard_text, height=260)

                save_draft("클립보드 복사")
            except InsufficientTranscriptError as exc:
                if batch_job_id:
                    batch_item_status = "needs_review"
                    batch_item_detail = "원문이 짧아 직접 확인 필요"
                    batch_item_transcript_path = str(transcript_file or batch_item_transcript_path)
                diagnostics = st.session_state.get("summary_generation_diagnostics") or {}
                update_progress(
                    "정리본 생성",
                    "보완 필요",
                    f"원문 부족으로 보완 필요 · {diagnostics.get('transcript_length_chars', '-')}자",
                )
                set_troubleshooting_info(
                    "원문 부족",
                    exc,
                    selected_audio_path,
                    target_path,
                    {
                        "transcript_length_chars": diagnostics.get("transcript_length_chars", "-"),
                        "transcript_word_count": diagnostics.get("transcript_word_count", "-"),
                        "transcript_sentence_count": diagnostics.get("transcript_sentence_count", "-"),
                        "insufficient_transcript": diagnostics.get("insufficient_transcript", True),
                        "min_transcript_chars": diagnostics.get("min_transcript_chars", "-"),
                        "min_transcript_words": diagnostics.get("min_transcript_words", "-"),
                        "min_transcript_sentences": diagnostics.get("min_transcript_sentences", "-"),
                    },
                )
                st.session_state["post_run_warning"] = (
                    "원문이 너무 짧아 자동 정리본을 만들 근거가 부족합니다. "
                    "원문을 확인한 뒤 더 긴 녹음 파일로 다시 시도하거나, 직접 정리본을 작성해 주세요."
                )
                st.warning("원문이 너무 짧아 자동 정리본을 만들 근거가 부족합니다.")
                st.info("원문을 확인한 뒤 더 긴 녹음 파일로 다시 시도하거나, 직접 정리본을 작성해 주세요.")
                save_draft("원문 부족")
            except WorkflowStepError as exc:
                if batch_job_id:
                    batch_item_transcript_path = str(transcript_file or batch_item_transcript_path)
                    if exc.stage == "정리본 생성":
                        pause_audio_batch = should_pause_audio_batch_for_codex_error(exc)
                        batch_item_status = "pending" if pause_audio_batch else "needs_review"
                    else:
                        batch_item_status = "failed"
                    batch_item_detail = str(exc).splitlines()[0][:160]
                update_progress(exc.stage, "실패", workflow_step_failure_progress_detail(exc.stage, exc))
                troubleshooting_extra = None
                if exc.stage == "정리본 생성" and "transcript" in locals():
                    error_text = str(exc)
                    stderr_match = re.search(r"stderr:\n(.+)", error_text, flags=re.DOTALL)
                    stdout_length_match = re.search(r"stdout 길이:\s*(\d+)", error_text)
                    stderr_length_match = re.search(r"stderr 길이:\s*(\d+)", error_text)
                    returncode_match = re.search(r"returncode:\s*([^\n]+)", error_text)
                    command_match = re.search(r"실행 명령:\s*(.+)", error_text)
                    troubleshooting_extra = {
                        "원문 글자 수": len(transcript or ""),
                        "원문 앞 300자 미리보기": (transcript or "")[:300],
                        "Codex returncode": returncode_match.group(1).strip() if returncode_match else "-",
                        "stdout 길이": stdout_length_match.group(1) if stdout_length_match else "-",
                        "stderr 길이": stderr_length_match.group(1) if stderr_length_match else "-",
                        "stderr 내용": stderr_match.group(1).strip() if stderr_match else "-",
                        "실제 실행 명령": command_match.group(1).strip() if command_match else "-",
                    }
                    summary_diagnostics = getattr(exc, "diagnostics", None)
                    if isinstance(summary_diagnostics, dict):
                        st.session_state["summary_generation_diagnostics"] = summary_diagnostics
                        troubleshooting_extra.update(
                            {
                                "first_attempt_valid": summary_diagnostics.get("first_attempt_valid", "-"),
                                "retry_attempted": summary_diagnostics.get("retry_attempted", "-"),
                                "retry_valid": summary_diagnostics.get("retry_valid", "-"),
                                "runtime_noise_removed": summary_diagnostics.get("runtime_noise_removed", "-"),
                                "removed_noise_line_count": summary_diagnostics.get("removed_noise_line_count", "-"),
                                "transcript_source": summary_diagnostics.get("transcript_source", "-"),
                                "transcript_length": summary_diagnostics.get("transcript_length", "-"),
                                "invalid_reason": summary_diagnostics.get("invalid_reason", "-"),
                            }
                        )
                elif exc.stage == "원문 추출":
                    extract_debug = st.session_state.get("last_extract_debug_info") or {}
                    troubleshooting_extra = {
                        "실제 예외 type": extract_debug.get("exception_type", "-"),
                        "실제 예외 message": extract_debug.get("exception_message", "-"),
                        "변환 wav 경로": extract_debug.get("converted_wav_path", "-"),
                        "Whisper 입력 경로": extract_debug.get("whisper_audio_path", "-"),
                        "vad_filter=False 재시도": "예" if extract_debug.get("retry_used") else "아니오",
                        "디버그 로그 파일": extract_debug.get("debug_log_path", "-"),
                    }
                set_troubleshooting_info(exc.stage, exc, selected_audio_path, target_path, troubleshooting_extra)
                if exc.stage == "저장 파일 준비":
                    st.session_state["post_run_error"] = "원문 저장 파일 준비 단계에서 실패했습니다."
                    st.error("원문 저장 파일 준비 단계에서 실패했습니다.")
                elif exc.stage == "원문 추출":
                    st.session_state["post_run_error"] = "원문 추출 단계에서 실패했습니다."
                    st.error("원문 추출 단계에서 실패했습니다.")
                elif exc.stage == "원문 저장":
                    st.session_state["post_run_error"] = "원문 저장 단계에서 실패했습니다."
                    st.error("원문 저장 단계에서 실패했습니다.")
                elif exc.stage == "정리본 생성":
                    summary_diagnostics = getattr(exc, "diagnostics", None)
                    invalid_reason = ""
                    if isinstance(summary_diagnostics, dict):
                        st.session_state["summary_generation_diagnostics"] = summary_diagnostics
                        invalid_reason = str(summary_diagnostics.get("invalid_reason") or "")
                    if invalid_reason in {"missing_specific_today_check", "missing_today_focus"}:
                        draft_summary = getattr(exc, "summary", "") or ""
                        set_needs_completion_summary_state(draft_summary, summary_diagnostics if isinstance(summary_diagnostics, dict) else {})
                        update_progress("정리본 생성", "보완 필요", summary_invalid_reason_label(invalid_reason))
                        st.session_state["post_run_warning"] = (
                            "정리본의 상담 참고 내용이 유효하지 않아 자동 저장을 보류했습니다. "
                            "상담 참고사항을 확인해 주세요."
                        )
                        st.warning("정리본 보완 필요")
                        st.info("상담 참고사항을 확인한 뒤 다시 저장하세요.")
                    else:
                        clear_failed_summary_state()
                        if is_codex_authentication_error(exc):
                            st.session_state["codex_login_required"] = True
                            update_progress("정리본 생성", "보완 필요", "Codex 로그인 필요")
                            st.session_state["post_run_warning"] = (
                                "Codex 로그인이 풀려 자동 정리본 생성을 멈췄습니다. "
                                "PowerShell에서 codex login을 실행한 뒤 정리본만 다시 생성하세요."
                            )
                            st.warning("Codex 로그인이 필요합니다.")
                            st.info("로그인 후 정리본만 다시 생성하거나, 아래에서 정리본을 직접 입력하세요.")
                        elif is_codex_network_error(exc):
                            update_progress("정리본 생성", "보완 필요", "Codex 네트워크 연결 실패")
                            st.session_state["post_run_warning"] = (
                                "Codex 네트워크 연결이 차단돼 자동 정리본 생성을 멈췄습니다. "
                                "앱을 다시 시작한 뒤 정리본만 다시 생성하세요."
                            )
                            st.warning("Codex 네트워크 연결이 차단됐습니다.")
                            st.info("앱을 다시 시작한 뒤 재시도하거나, 아래에서 정리본을 직접 입력하세요.")
                        else:
                            st.session_state["post_run_error"] = (
                                "정리본 생성 실패: 원문은 저장됐지만 정리본이 생성되지 않았습니다. "
                                "정리본 칸을 비웠습니다. 정리본만 다시 생성을 눌러 재시도하세요."
                            )
                            st.error("정리본 생성 실패: 원문은 저장됐지만 정리본이 생성되지 않았습니다.")
                            st.info("정리본 칸을 비웠습니다. 정리본만 다시 생성을 눌러 재시도하세요.")
                    summary_prompt = getattr(exc, "prompt", None) or (prompt if "prompt" in locals() else "")
                    if show_developer_diagnostics and summary_prompt:
                        st.text_area("Codex에 직접 붙여넣을 프롬프트", summary_prompt, height=220)
                else:
                    st.session_state["post_run_error"] = f"{exc.stage} 단계에서 실패했습니다."
                    st.error(f"{exc.stage} 단계에서 실패했습니다.")
                if st.session_state.get("summary_status") != SUMMARY_STATUS_NEEDS_COMPLETION:
                    render_troubleshooting_info()
            except Exception as exc:
                if batch_job_id:
                    batch_item_status = "failed"
                    batch_item_detail = str(exc).splitlines()[0][:160] or type(exc).__name__
                    batch_item_transcript_path = str(transcript_file or batch_item_transcript_path)
                update_progress("원문 저장", "실패", str(exc))
                set_troubleshooting_info("원문 저장", exc, selected_audio_path, target_path)
                st.session_state["post_run_error"] = "원문 저장 단계에서 실패했습니다."
                st.error("원문 저장 단계에서 실패했습니다.")
                render_troubleshooting_info()
            finally:
                if batch_job_id:
                    finish_active_audio_batch_job(
                        status=batch_item_status,
                        detail=batch_item_detail,
                        transcript_path=batch_item_transcript_path,
                        summary=batch_item_summary,
                        pause_batch=pause_audio_batch,
                    )
                else:
                    st.session_state["is_processing"] = False
                    st.session_state["pending_main_run"] = False
                save_draft(st.session_state.get("last_step", ""))
                st.rerun()

        transcript_display_text = st.session_state.get("transcript_text") or ""
        if show_developer_diagnostics and transcript_display_text.strip():
            with st.expander("추출된 원문 보기", expanded=False):
                segment_rows = st.session_state.get("transcript_segments")
                safe_segment_rows = segment_rows or []
                st.text_area("추출된 원문", transcript_display_text, height=220)
                if safe_segment_rows:
                    st.markdown("세그먼트별 원문")
                    for row in safe_segment_rows:
                        st.write(f"[{row['start']} - {row['end']}] {row['text']}")

        summary_diagnostics = st.session_state.get("summary_generation_diagnostics") or {}
        if show_developer_diagnostics and summary_diagnostics:
            with st.expander("정리본 생성 진단", expanded=False):
                st.json(
                    {
                        "first_attempt_valid": summary_diagnostics.get("first_attempt_valid"),
                        "retry_attempted": summary_diagnostics.get("retry_attempted"),
                        "retry_valid": summary_diagnostics.get("retry_valid"),
                        "transcript_source": summary_diagnostics.get("transcript_source"),
                        "transcript_length": summary_diagnostics.get("transcript_length"),
                        "transcript_hash": summary_diagnostics.get("transcript_hash"),
                        "invalid_reason": summary_diagnostics.get("invalid_reason"),
                        "attempt_count": summary_diagnostics.get("attempt_count"),
                        "retry_context": summary_diagnostics.get("retry_context"),
                        "transcript_word_count": summary_diagnostics.get("transcript_word_count"),
                        "transcript_sentence_count": summary_diagnostics.get("transcript_sentence_count"),
                        "insufficient_transcript": summary_diagnostics.get("insufficient_transcript"),
                        "thresholds": {
                            "chars": summary_diagnostics.get("min_transcript_chars"),
                            "words": summary_diagnostics.get("min_transcript_words"),
                            "sentences": summary_diagnostics.get("min_transcript_sentences"),
                        },
                        "attempts": summary_diagnostics.get("attempts"),
                    }
                )

        existing_summary_value = st.session_state.get("latest_summary") or ""
        if existing_summary_value:
            normalized_existing_summary = normalize_summary(
                existing_summary_value,
                student_name,
                counseling_date,
            )
            if normalized_existing_summary != existing_summary_value:
                for key in list(st.session_state.keys()):
                    if str(key).startswith("edited_latest_summary_"):
                        st.session_state.pop(key, None)
                st.session_state["latest_summary"] = normalized_existing_summary
                existing_summary_value = normalized_existing_summary
            existing_summary_valid = is_valid_generated_summary(existing_summary_value)
            if existing_summary_valid and st.session_state.get("summary_status") == SUMMARY_STATUS_NEEDS_COMPLETION:
                st.session_state["summary_status"] = SUMMARY_STATUS_READY
                st.session_state["summary_generation_failed"] = False
                st.session_state["summary_retry_available"] = False
            elif (
                not existing_summary_valid
                and st.session_state.get("summary_status") != SUMMARY_STATUS_NEEDS_COMPLETION
            ):
                clear_failed_summary_state()

        if st.session_state.get("summary_status") == SUMMARY_STATUS_INSUFFICIENT_TRANSCRIPT:
            st.warning("원문이 너무 짧아 자동 정리본을 만들 근거가 부족합니다.")
            st.info("원문 부족 상태에서는 자동 정리본을 복사/저장할 수 없습니다.")
            st.caption("원문을 확인한 뒤 더 긴 녹음 파일로 다시 시도하거나, 아래 형식으로 직접 정리본을 작성해 주세요.")
            st.code(
                "[오늘 확인할 것]\n\n"
                "수업 전 확인\n"
                "- 구체 확인 항목\n\n"
                "*상담 참고사항*\n"
                "- 상담 내용",
                language="text",
            )
            render_troubleshooting_info()

        if st.session_state.get("summary_status") == SUMMARY_STATUS_NEEDS_COMPLETION:
            st.warning("정리본 보완 필요")
            st.info(
                "정리본의 상담 참고 내용이 유효하지 않습니다. "
                "상담 참고사항을 확인한 뒤 다시 저장하세요."
            )
            st.caption("구체 확인 항목이 없는 것은 정상이며, 의미 있는 상담 참고사항만 있으면 저장할 수 있습니다.")
            st.code(
                "[오늘 확인할 것]\n\n"
                "수업 전 확인\n"
                "\n"
                "*상담 참고사항*\n"
                "- 상담 참고 내용",
                language="text",
            )
            if show_developer_diagnostics:
                render_troubleshooting_info()

        if st.session_state.get("summary_generation_failed"):
            st.warning("정리본이 없어 복사/저장할 수 없습니다. 자동 재시도하거나 직접 입력하세요.")
            retry_transcript_text = (
                st.session_state.get("transcript_text")
                or read_saved_transcript_body(st.session_state.get("current_transcript_path"))
            )
            retry_disabled = ui_locked or not retry_transcript_text.strip()
            if st.button("정리본만 다시 생성", disabled=retry_disabled):
                st.session_state["is_processing"] = True
                reset_progress()
                render_progress(status_placeholder.container())
                retry_transcript_path = st.session_state.get("current_transcript_path") or ""
                retry_target_path = Path(retry_transcript_path) if retry_transcript_path else None
                try:
                    update_progress("정리본 생성", "진행 중", "저장된 원문으로 재시도")
                    summary_result = generate_summary_from_transcript(
                        student_name,
                        counseling_date,
                        retry_transcript_text,
                        summary_style,
                        use_academy_glossary,
                        max_invalid_retries=1,
                        source_transcript_path=retry_target_path,
                        retry_context="manual_retry",
                        transcript_source="saved_transcript_retry",
                        on_retry=lambda reason: update_progress(
                            "정리본 생성",
                            "진행 중",
                            summary_retry_progress_message(reason),
                        ),
                    )
                    summary = str(summary_result["summary"])
                    prompt = str(summary_result["prompt"])
                    st.session_state["transcript_text"] = retry_transcript_text
                    st.session_state["latest_summary"] = summary
                    st.session_state["summary_generation_failed"] = False
                    st.session_state["summary_status"] = SUMMARY_STATUS_READY
                    st.session_state["summary_retry_available"] = False
                    st.session_state["summary_generation_diagnostics"] = summary_result.get("diagnostics", {})
                    update_progress("정리본 생성", "완료", "저장된 원문으로 재생성")
                    clipboard_text = summary
                    copied, copy_error = copy_to_clipboard(clipboard_text)
                    if copied:
                        st.session_state["post_run_toast"] = (
                            "정리본만 다시 생성했습니다. 정리본 전체가 클립보드에 복사되었습니다."
                        )
                        st.session_state["post_run_success"] = ""
                        update_progress("클립보드 복사", "완료", "정리본 전체 복사 완료")
                    else:
                        st.session_state["post_run_warning"] = (
                            "정리본만 다시 생성했습니다. 클립보드 복사는 실패했으니 정리본 확인 및 수정 영역에서 확인하세요."
                        )
                        if copy_error:
                            st.session_state["post_run_warning"] += f" ({copy_error})"
                    save_draft("정리본 재생성")
                except Exception as exc:
                    summary_diagnostics = getattr(exc, "diagnostics", None)
                    invalid_reason = ""
                    if isinstance(summary_diagnostics, dict):
                        st.session_state["summary_generation_diagnostics"] = summary_diagnostics
                        invalid_reason = str(summary_diagnostics.get("invalid_reason") or "")
                    if invalid_reason in {"missing_specific_today_check", "missing_today_focus"}:
                        draft_summary = getattr(exc, "summary", "") or ""
                        set_needs_completion_summary_state(draft_summary, summary_diagnostics if isinstance(summary_diagnostics, dict) else {})
                        set_troubleshooting_info("정리본 보완 필요", exc, selected_audio_path, retry_target_path)
                        st.session_state["post_run_warning"] = (
                            "정리본의 상담 참고 내용이 유효하지 않아 자동 저장을 보류했습니다. "
                            "상담 참고사항을 확인해 주세요."
                        )
                    else:
                        clear_failed_summary_state()
                        set_troubleshooting_info("정리본 생성", exc, selected_audio_path, retry_target_path)
                        if is_codex_authentication_error(exc):
                            st.session_state["codex_login_required"] = True
                            update_progress("정리본 생성", "보완 필요", "Codex 로그인 필요")
                            st.session_state["post_run_warning"] = (
                                "Codex 로그인이 풀려 재생성을 멈췄습니다. "
                                "PowerShell에서 codex login을 실행한 뒤 다시 시도하세요."
                            )
                        elif is_codex_network_error(exc):
                            update_progress("정리본 생성", "보완 필요", "Codex 네트워크 연결 실패")
                            st.session_state["post_run_warning"] = (
                                "Codex 네트워크 연결이 차단돼 재생성을 멈췄습니다. "
                                "앱을 다시 시작한 뒤 다시 시도하세요."
                            )
                        else:
                            st.session_state["post_run_error"] = (
                                "정리본 생성 실패: 원문은 유지됐지만 정리본이 생성되지 않았습니다. "
                                "정리본 칸을 비웠습니다."
                            )
                    summary_prompt = getattr(exc, "prompt", None) or (prompt if "prompt" in locals() else "")
                    if show_developer_diagnostics and summary_prompt:
                        st.session_state["last_summary_retry_prompt"] = summary_prompt
                finally:
                    st.session_state["is_processing"] = False
                    st.rerun()

            with st.expander("직접 정리본 입력", expanded=False):
                st.caption("자동 생성이 계속 실패할 때 저장된 원문을 확인해 정리본을 직접 작성할 수 있습니다.")
                manual_summary_text = st.text_area(
                    "정리본",
                    key="manual_summary_fallback_text",
                    height=260,
                    placeholder="*상담 참고사항*\n- 상담 내용을 입력하세요.",
                )
                manual_result = validate_manual_summary_candidate(
                    manual_summary_text,
                    student_name,
                    counseling_date,
                )
                if st.button(
                    "직접 입력한 정리본 사용",
                    key="use_manual_summary_fallback",
                    disabled=ui_locked or not manual_summary_text.strip(),
                ):
                    invalid_reason = manual_result["invalid_reason"]
                    if invalid_reason:
                        st.warning(f"정리본을 확인해 주세요: {summary_invalid_reason_label(invalid_reason)}")
                    else:
                        manual_summary = manual_result["summary"]
                        st.session_state["latest_summary"] = manual_summary
                        st.session_state["summary_generation_failed"] = False
                        st.session_state["summary_status"] = SUMMARY_STATUS_READY
                        st.session_state["summary_retry_available"] = False
                        st.session_state["summary_generation_diagnostics"] = {
                            "source": "manual_fallback",
                            **transcript_generation_signature(retry_transcript_text),
                        }
                        update_progress("정리본 생성", "완료", "직접 입력한 정리본 사용")
                        copied, copy_error = copy_to_clipboard(manual_summary)
                        if copied:
                            st.session_state["post_run_toast"] = (
                                "직접 입력한 정리본을 사용합니다. 정리본 전체가 클립보드에 복사되었습니다."
                            )
                        else:
                            st.session_state["post_run_warning"] = (
                                "정리본은 저장됐지만 클립보드 복사에 실패했습니다."
                            )
                            if copy_error:
                                st.session_state["post_run_warning"] += f" ({copy_error})"
                        save_draft("직접 정리본 입력")
                        st.rerun()

        pending_admin_sync: AdminNotesSyncRequest | None = None
        safe_summary = st.session_state.get("latest_summary") or ""
        batch_state = st.session_state.get("audio_batch_state")
        batch_summary_visible = bool(batch_state and any(
            job["transcript_path"] == st.session_state.get("current_transcript_path")
            and job["student_name"] == student_name
            and job["counseling_date"] == counseling_date.isoformat()
            for job in batch_state["jobs"]
        ))
        if safe_summary and not batch_summary_visible:
            summary_key = f"edited_latest_summary_{abs(hash(safe_summary))}"
            needs_completion = st.session_state.get("summary_status") == SUMMARY_STATUS_NEEDS_COMPLETION
            with st.expander("정리본 확인 및 수정", expanded=True):
                edited_text = st.text_area(
                    "정리본 보완" if needs_completion else "정리본 수정",
                    safe_summary,
                    height=360,
                    key=summary_key,
                )
                if needs_completion:
                    formatted_edited_text = format_summary_section_spacing(edited_text)
                    completion_reason = generated_summary_invalid_reason(formatted_edited_text)
                    if st.button("보완한 정리본 사용", disabled=ui_locked):
                        if completion_reason:
                            st.warning(f"아직 보완이 필요합니다: {summary_invalid_reason_label(completion_reason)}")
                        else:
                            st.session_state["latest_summary"] = formatted_edited_text
                            st.session_state["summary_generation_failed"] = False
                            st.session_state["summary_status"] = SUMMARY_STATUS_READY
                            st.session_state["summary_retry_available"] = False
                            update_progress("정리본 생성", "완료", "직접 보완 완료")
                            copied, copy_error = copy_to_clipboard(formatted_edited_text)
                            if copied:
                                update_progress("클립보드 복사", "완료", "정리본 전체 복사 완료")
                                st.session_state["post_run_toast"] = (
                                    "보완한 정리본이 유효합니다. 정리본 전체가 클립보드에 복사되었습니다."
                                )
                                st.session_state["post_run_success"] = ""
                            else:
                                st.session_state["post_run_warning"] = (
                                    "보완한 정리본이 유효합니다. 클립보드 복사는 실패했으니 정리본 확인 및 수정 영역에서 직접 복사하세요."
                                )
                                if copy_error:
                                    st.session_state["post_run_warning"] += f" ({copy_error})"
                            save_draft("정리본 보완 완료")
                            st.rerun()
                    if completion_reason:
                        st.caption(f"현재 보완 사유: {summary_invalid_reason_label(completion_reason)}")
                today_focus_text = extract_today_focus(edited_text)
                pending_admin_sync = render_admin_notes_save_ui(
                    student_name=student_name,
                    counseling_date_value=counseling_date,
                    today_focus_text=today_focus_text,
                    summary_text=edited_text,
                    export_allowed=summary_copy_save_allowed(
                        edited_text,
                        st.session_state.get("summary_status"),
                    ),
                )
                if show_developer_diagnostics:
                    if st.button("수정본 복사하기"):
                        formatted_edited_text = format_summary_section_spacing(edited_text)
                        copied, copy_error = copy_to_clipboard(formatted_edited_text)
                        if copied:
                            st.session_state["latest_summary"] = formatted_edited_text
                            st.success("수정본이 클립보드에 복사되었습니다. Ctrl+V로 붙여넣으세요.")
                        else:
                            st.error(f"수정본 클립보드 복사에 실패했습니다: {copy_error}")

    with log_csv_container:
        render_log_csv_to_docs_preview_ui(
            history_summary_placeholder,
            pending_admin_sync,
        )


if __name__ == "__main__":
    main()
