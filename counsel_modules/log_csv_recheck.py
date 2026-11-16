from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from time import monotonic
from typing import Literal

import streamlit as st

from counsel_modules.log_csv_processing_history import LogCsvHistoryEntry


@dataclass(frozen=True, slots=True)
class LogCsvStorageResponse:
    ok: bool
    verify_only: bool
    found_existing: bool | None


@dataclass(frozen=True, slots=True)
class LogCsvStorageCheck:
    source_index: int
    outcome: Literal["duplicate", "failure", "review"]
    message: str
    verified_summary: str = ""


@dataclass(frozen=True, slots=True)
class LogCsvRecheckActions:
    indices: tuple[int, ...]
    enabled: bool
    verify: Callable[[int], LogCsvStorageCheck]
    persist: Callable[[list[LogCsvStorageCheck]], bool]
    rerun: Callable[[], None]
    document_key: Callable[[int], str] = str


@dataclass(frozen=True, slots=True)
class LogCsvRecheckPolicy:
    max_workers: int = 5
    deadline_seconds: float = 60


@dataclass(frozen=True, slots=True)
class LogCsvRecheckProgress:
    completed: int
    total: int
    elapsed_seconds: float


def log_csv_recheck_indices(
    entry: LogCsvHistoryEntry, source_groups: tuple[tuple[int, ...], ...],
) -> tuple[int, ...]:
    unresolved = tuple(index for index, group in enumerate(source_groups)
        if any(entry["outcomes"][source] in {"failure", "review"} for source in group))
    if unresolved:
        return unresolved
    return tuple(index for index, group in enumerate(source_groups)
        if any(entry["outcomes"][source] in {"success", "duplicate"} for source in group))


def collect_log_csv_storage_checks(
    actions: LogCsvRecheckActions,
    on_progress: Callable[[LogCsvRecheckProgress], None],
    policy: LogCsvRecheckPolicy = LogCsvRecheckPolicy(),
) -> list[LogCsvStorageCheck]:
    """Bound read-only checks; unfinished reads cannot later persist a result."""
    indices = tuple(dict.fromkeys(actions.indices))
    if not indices:
        return []
    started = monotonic()
    deadline = started + policy.deadline_seconds
    remaining = list(indices)
    results: dict[int, LogCsvStorageCheck] = {}
    active_keys: set[str] = set()
    futures: dict[Future[LogCsvStorageCheck], tuple[int, str]] = {}
    workers = max(1, min(policy.max_workers, len(indices)))
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="connect-log-docs-recheck")

    def submit_available() -> None:
        for index in list(remaining):
            if len(futures) >= workers or monotonic() >= deadline:
                break
            key = actions.document_key(index)
            if key in active_keys:
                continue
            remaining.remove(index)
            active_keys.add(key)
            futures[executor.submit(actions.verify, index)] = (index, key)

    def report() -> None:
        on_progress(LogCsvRecheckProgress(len(results), len(indices), monotonic() - started))

    try:
        report()
        submit_available()
        while futures and monotonic() < deadline:
            finished, _ = wait(futures, timeout=min(0.25, max(0, deadline - monotonic())), return_when=FIRST_COMPLETED)
            for future in finished:
                index, key = futures.pop(future)
                active_keys.remove(key)
                results[index] = future.result()
            report()
            submit_available()
    finally:
        for future in futures:
            future.cancel()
        # In-flight HTTP reads may finish, but only this UI thread can persist checks.
        executor.shutdown(wait=False, cancel_futures=True)
    return [results.get(index) or LogCsvStorageCheck(
        index, "review", "확인 대기 시간이 지나 저장 여부를 확정하지 못했습니다. 자동 재전송하지 않습니다.",
    ) for index in indices]


def classify_log_csv_storage_check(
    source_index: int, response: LogCsvStorageResponse,
) -> LogCsvStorageCheck:
    if response.ok and response.verify_only and response.found_existing is True:
        return LogCsvStorageCheck(source_index, "duplicate", "문서에서 저장된 내용을 다시 확인했습니다.")
    if response.ok and response.verify_only and response.found_existing is False:
        return LogCsvStorageCheck(source_index, "failure", "현재 문서에 저장할 내용이 없습니다. 다시 저장해 주세요.")
    return LogCsvStorageCheck(source_index, "review", "문서 저장 여부를 다시 확인하지 못했습니다.")


def verify_log_csv_storage_candidates(
    source_index: int,
    summaries: tuple[str, ...],
    verify: Callable[[str], LogCsvStorageResponse],
) -> LogCsvStorageCheck:
    uncertain = not summaries
    for summary in dict.fromkeys(summaries):
        check = classify_log_csv_storage_check(source_index, verify(summary))
        if check.outcome == "duplicate":
            return LogCsvStorageCheck(source_index, check.outcome, check.message, summary)
        uncertain = uncertain or check.outcome == "review"
    return classify_log_csv_storage_check(
        source_index, LogCsvStorageResponse(not uncertain, True, False),
    )


def render_completed_log_csv_recheck(
    entry: LogCsvHistoryEntry, actions: LogCsvRecheckActions,
) -> None:
    st.caption(f"재확인 대상 {len(actions.indices)}건")
    button_slot = st.empty()
    if not button_slot.button(
        "Docs 저장 다시 확인", icon=":material/refresh:",
        disabled=not actions.enabled or not actions.indices,
        key=f"log_csv_completed_recheck_{entry['csv_hash']}",
    ):
        return
    button_slot.button("Docs 저장 확인 중", icon=":material/refresh:", disabled=True,
                       key=f"log_csv_recheck_running_{entry['csv_hash']}")
    progress = st.progress(0.0, text=f"Docs 확인 0/{len(actions.indices)}건")

    def show_progress(value: LogCsvRecheckProgress) -> None:
        progress.progress(value.completed / max(1, value.total),
            text=f"Docs 확인 {value.completed}/{value.total}건 · {int(value.elapsed_seconds)}초 경과")

    with st.spinner("Docs 저장 확인 중"):
        checks = collect_log_csv_storage_checks(actions, show_progress)
        if not actions.persist(checks):
            st.error("재확인 결과를 저장하지 못했습니다. 기존 기록은 유지됩니다.")
            return
    actions.rerun()
