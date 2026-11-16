from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from counsel_modules.audio_batch import AudioBatchJob, AudioBatchState, update_audio_batch_job
from counsel_modules.text_utils import format_summary_section_spacing


@dataclass(frozen=True, slots=True)
class AudioBatchReviewActions:
    persist: Callable[[AudioBatchState], None]
    render_target: Callable[[AudioBatchJob], None]
    download_text: Callable[[AudioBatchState], str]


@st.fragment
def render_audio_batch_summary_review(
    state: AudioBatchState, ui_locked: bool, actions: AudioBatchReviewActions,
) -> None:
    choice_key = "audio_batch_summary_choice"
    restore_key = "audio_batch_summary_restore_choice"
    if "audio_batch_state" in st.session_state:
        current: AudioBatchState | None = st.session_state["audio_batch_state"]
        if current is None or current.get("acknowledged_at"):
            st.session_state.pop(restore_key, None)
            return
        if current != state:
            # A fragment escalated before widget rendering can lose widget-owned keys.
            st.session_state[restore_key] = st.session_state.get(choice_key)
            st.rerun(scope="app")
            return
    jobs = [job for job in state["jobs"] if job["summary"].strip() or (
        job["transcript_path"] and job["status"] == "needs_review"
    )]
    if not jobs:
        st.caption("아직 완료된 정리본이 없습니다.")
        return
    job_ids = [job["job_id"] for job in jobs]
    restored_choice = st.session_state.pop(restore_key, None)
    if restored_choice in job_ids:
        st.session_state[choice_key] = restored_choice
    if st.session_state.get(choice_key) not in job_ids:
        st.session_state[choice_key] = job_ids[0]
    position = job_ids.index(st.session_state[choice_key])
    st.html("""
        <style>
        .st-key-audio-batch-review-nav [data-testid="stHorizontalBlock"] {
            display:grid; grid-template-columns:40px minmax(0,1fr) 40px;
            gap:8px; align-items:start;
        }
        .st-key-audio-batch-review-nav [data-testid="stColumn"] { width:100%; min-width:0; }
        </style>
    """)
    with st.container(key="audio-batch-review-nav"):
        previous, selector, following = st.columns([1, 8, 1], gap="small")
        with previous:
            st.button("", icon=":material/chevron_left:", help="이전 정리본",
                      key="audio_batch_summary_previous", disabled=ui_locked or position == 0,
                      width="stretch", on_click=st.session_state.update,
                      args=({choice_key: job_ids[max(0, position - 1)]},))
        with selector:
            st.selectbox("정리본 선택", job_ids, key=choice_key, label_visibility="collapsed",
                         disabled=ui_locked, format_func=lambda job_id: next(
                             f"{job['student_name']} · {job['source_name']}" for job in jobs
                             if job["job_id"] == job_id))
        with following:
            st.button("", icon=":material/chevron_right:", help="다음 정리본",
                      key="audio_batch_summary_next", disabled=ui_locked or position == len(jobs) - 1,
                      width="stretch", on_click=st.session_state.update,
                      args=({choice_key: job_ids[min(len(jobs) - 1, position + 1)]},))
    selected = jobs[position]
    st.caption(f"{position + 1} / {len(jobs)} · {selected['student_name']} · {selected['counseling_date']} · "
               + ("ADMIN 저장 완료" if selected.get("admin_saved") else "저장 대기"))
    widget_key = f"audio_batch_review_{selected['job_id']}"
    st.session_state[widget_key] = selected["summary"]

    def persist_edit() -> None:
        current: AudioBatchState | None = st.session_state.get("audio_batch_state")
        # A queued edit can arrive after completion confirmation retires the batch.
        if current is None or current.get("acknowledged_at"):
            return
        job = next((item for item in current["jobs"] if item["job_id"] == selected["job_id"]), None)
        if job is None:
            return
        summary = format_summary_section_spacing(st.session_state[widget_key])
        if summary != job["summary"]:
            updated = update_audio_batch_job(
                current, job["job_id"], status="completed" if summary else "needs_review",
                detail="정리본 수정 · 저장 대기" if summary else "정리본 입력 필요",
                transcript_path=job["transcript_path"], summary=summary,
                updated_at=datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
            )
            actions.persist(updated)

    st.text_area("정리본 수정", key=widget_key, height=360, disabled=ui_locked, on_change=persist_edit)
    actions.render_target(selected)
    st.download_button("완료 정리본 묶음 다운로드", data=actions.download_text(state).encode("utf-8-sig"),
                       file_name=f"5A_정리본_일괄_{selected['counseling_date']}.txt", mime="text/plain",
                       width="stretch", on_click="ignore")
