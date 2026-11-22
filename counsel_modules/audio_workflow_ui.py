from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass

import streamlit as st

from counsel_modules.audio_batch import AudioBatchJob, AudioBatchState
from counsel_modules.dual_save_runtime import enabled as dual_save_enabled


@dataclass(frozen=True, slots=True)
class AudioWorkflowCounts:
    total: int
    generated: int
    saved: int
    students: int
    total_students: int
    generated_students: int
    pending: int
    active: int
    review: int
    failed: int
    admin_saved: int = 0
    docs_saved: int = 0
    dual: bool = False

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.saved == self.total


def audio_workflow_counts(state: AudioBatchState) -> AudioWorkflowCounts:
    jobs = state["jobs"]
    dual = dual_save_enabled()
    admin_saved = [
        job for job in jobs if job.get("admin_saved") and job.get("admin_sid")
        and (bool(job.get("dual_save_sid")) if dual else job["status"] == "completed")
    ]
    docs_saved = [job for job in jobs if dual and job.get("docs_saved") and job.get("dual_save_sid")]
    saved = [job for job in admin_saved if not dual or job.get("docs_saved")]
    sids_by_name: dict[str, set[str]] = {}
    for job in jobs:
        if job.get("admin_sid"):
            sids_by_name.setdefault(job["student_name"].strip(), set()).add(job["admin_sid"])
    groups: dict[tuple[str, str], list[AudioBatchJob]] = {}
    for job in jobs:
        name = job["student_name"].strip()
        sid = job.get("admin_sid", "")
        known_sids = sids_by_name.get(name, set())
        if not sid and len(known_sids) == 1:
            sid = next(iter(known_sids))
        # An unresolved homonym must not be merged into either confirmed target.
        identity = ("sid", sid) if sid else ("name", name) if name and not known_sids else ("job", job["job_id"])
        groups.setdefault(identity, []).append(job)
    return AudioWorkflowCounts(
        total=len(jobs), generated=sum(bool(job["summary"].strip()) for job in jobs),
        saved=len(saved), students=len({job["admin_sid"] for job in saved}),
        total_students=len(groups),
        generated_students=sum(all(job["summary"].strip() for job in group) for group in groups.values()),
        pending=sum(job["status"] == "pending" for job in jobs),
        active=sum(job["status"] == "processing" for job in jobs),
        review=sum(job["status"] == "needs_review" for job in jobs),
        failed=sum(job["status"] == "failed" for job in jobs),
        admin_saved=len(admin_saved), docs_saved=len(docs_saved), dual=dual,
    )


def render_audio_workflow_overview(state: AudioBatchState) -> None:
    counts = audio_workflow_counts(state)
    batch_date = state["jobs"][0]["counseling_date"] if state["jobs"] else ""
    st.caption(f"{batch_date} · 녹음 {counts.total}개")
    if counts.dual:
        st.markdown(f"**양쪽 저장 {counts.saved}건** · 미저장 {counts.total - counts.saved}건")
    else:
        st.markdown(f"**ADMIN 저장 {counts.students}명** · 미저장 {counts.total - counts.saved}건")
    st.progress(counts.generated / max(counts.total, 1), text=f"정리본 {counts.generated}/{counts.total}개")
    st.progress(counts.admin_saved / max(counts.total, 1), text=f"ADMIN 저장 확인 {counts.admin_saved}/{counts.total}건")
    if counts.dual:
        st.progress(counts.docs_saved / max(counts.total, 1), text=f"Docs 저장 확인 {counts.docs_saved}/{counts.total}건")
    if counts.complete:
        destination = "ADMIN·Docs" if counts.dual else "ADMIN"
        st.success(f"전체 {counts.total}개 처리 완료 · {destination} {counts.students}명 저장 확인", icon=":material/check_circle:")
    elif counts.failed:
        st.error(f"처리 실패 {counts.failed}건 · 확인 필요 {counts.review}건", icon=":material/error:")
    elif counts.review:
        st.warning(f"확인 필요 {counts.review}건 · 나머지 작업은 계속 처리합니다." if counts.pending or counts.active else f"확인 필요 {counts.review}건 · 저장 결과 미완료", icon=":material/info:")
    elif not counts.active and not counts.pending:
        st.info("정리본 생성 완료 · ADMIN 저장 대기", icon=":material/sync:")


def render_audio_processing_log(
    state: AudioBatchState | None,
    progress: Mapping[str, str] | None = None,
    details: Mapping[str, str] | None = None,
) -> None:
    with st.popover("처리 로그", icon=":material/receipt_long:"):
        if progress and any(value != "대기" for value in progress.values()):
            st.dataframe([{"단계": stage, "상태": status, "내용": (details or {}).get(stage, "")}
                          for stage, status in progress.items() if status != "대기"],
                         hide_index=True, width="stretch")
        if not state:
            st.caption("현재 처리 기록이 없습니다.")
            return
        rows = [{
            "학생": job["student_name"], "파일": job["source_name"],
            "ADMIN": "저장 확인" if job.get("admin_saved") else "미저장",
            "상태": {"pending": "대기", "processing": "처리 중", "completed": "정리본 생성 완료", "needs_review": "확인 필요", "failed": "실패"}[job["status"]],
            "내용": job["detail"], "시각": job["updated_at"],
        } for job in state["jobs"]]
    if dual_save_enabled():
        for row, job in zip(rows, state["jobs"], strict=True):
            row["Docs"] = "저장 확인" if job.get("docs_saved") else "미저장"
        st.dataframe(rows, hide_index=True, width="stretch", height=min(360, 36 * len(rows) + 40))
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        st.download_button("로그 내려받기", buffer.getvalue().encode("utf-8-sig"),
                           file_name="5A_ADMIN_처리로그.csv", mime="text/csv", icon=":material/download:")
