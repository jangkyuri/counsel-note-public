"""Local Streamlit review surface, independent of legacy external integrations."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path

import streamlit as st

from hdhcounsel.config import CounselError, Settings
from hdhcounsel.service import transcribe
from hdhcounsel.storage import Record


def main() -> None:
    st.set_page_config(page_title="HDHcounsel", layout="wide")
    st.title("HDHcounsel")
    st.caption("로컬 한국어 전사 · 화자별 기록 · 상담 검수")
    st.info("녹음은 이 PC에서 처리합니다. 원문과 검수 기록만 로컬에 저장하며 외부 서비스에 전송하지 않습니다.")
    try:
        config_path = os.environ.get("HDHCOUNSEL_CONFIG")
        settings = Settings.load(Path(config_path) if config_path else None)
    except CounselError as exc:
        st.error(str(exc))
        return

    with st.sidebar:
        st.header("처리 설정")
        st.caption("WhisperX 3.8.6 · large-v3 · 한국어 정렬")
        st.write(f"장치: {settings.device} / {settings.compute_type}")
        diarize = st.checkbox("화자 분리", value=settings.diarize)
        fixed_speakers = st.checkbox("화자 수를 알고 있음", value=settings.num_speakers is not None)
        count = st.number_input(
            "화자 수", min_value=1, max_value=20, value=settings.num_speakers or 2,
            disabled=not fixed_speakers,
        )
        settings = replace(
            settings, diarize=diarize,
            num_speakers=int(count) if fixed_speakers and diarize else None,
        )
        st.caption("SPEAKER_00 같은 번호는 파일마다 달라집니다. 상담자/학생 이름은 직접 확인하세요.")
        st.code(str(settings.output_dir), language=None)
    assets_ready = True
    try:
        settings.validate_assets()
    except CounselError as exc:
        assets_ready = False
        st.warning(str(exc))
        st.code("hdhcounsel prepare", language="powershell")

    left, right = st.columns([1, 2], gap="large")
    with left:
        st.subheader("상담 녹음")
        student = st.text_input("학생명 또는 관리용 별칭")
        counseling_date = st.date_input("상담일", value=date.today())
        uploaded = st.file_uploader(
            "녹음 선택", type=["wav", "mp3", "m4a", "flac", "ogg", "aac", "amr", "3gp", "3ga"],
        )
        if uploaded is not None:
            st.audio(uploaded)
        if st.button("로컬 전사 시작", type="primary", disabled=uploaded is None or not assets_ready):
            try:
                with st.spinner("음성 인식 → 한국어 정렬 → 화자 분리 중입니다."):
                    with tempfile.TemporaryDirectory(prefix="hdhcounsel-upload-") as folder:
                        source = Path(folder) / ("input" + Path(uploaded.name).suffix.lower())
                        source.write_bytes(uploaded.getvalue())
                        transcript = transcribe(source, settings)
                    record = Record.create(
                        settings.output_dir, student.strip(), counseling_date.isoformat(), transcript,
                    )
                st.session_state["record"] = record
                st.session_state["notes"] = ""
                st.session_state["reviewed"] = False
                st.success("원문 저장 완료. 화자와 숫자·이름을 확인한 뒤 검수 기록을 저장하세요.")
            except (CounselError, OSError) as exc:
                st.error(str(exc))

    with right:
        st.subheader("원문과 검수 기록")
        record: Record | None = st.session_state.get("record")
        if record is None:
            st.write("녹음을 선택하고 전사를 시작하면 시간과 화자가 포함된 원문이 여기에 표시됩니다.")
            st.caption("기존 Google Docs / ADMIN / Codex 기능은 별도 legacy app.py에서만 실행됩니다.")
            return
        st.write(f"{record.student or '이름 미입력'} · {record.counseling_date}")
        if not record.transcript.segments:
            st.warning("인식된 발화가 없습니다. 원본 녹음의 음량과 내용을 확인하세요.")
        labels: dict[str, str] = {}
        speakers = sorted({row.speaker for row in record.transcript.segments if row.speaker})
        with st.expander("화자 이름 확인", expanded=bool(speakers)):
            for speaker in speakers:
                labels[speaker] = st.text_input(
                    speaker, value=speaker, key=f"{record.path.name}:{speaker}",
                ).strip() or speaker
        evidence = record.transcript.text(labels)
        st.text_area("시간·화자 포함 원문 (변경하지 않는 근거)", evidence, height=300, disabled=True)
        st.text_area("상담 정리 / 수정 사항 / 다음 확인할 일", key="notes", height=200)
        st.checkbox("원음과 대조하여 검수했습니다", key="reviewed")
        if st.button("검수 기록 저장"):
            try:
                record.save_review(st.session_state["notes"], labels, st.session_state["reviewed"])
                st.success("로컬 검수 기록 저장 완료")
            except OSError:
                st.error("검수 기록을 저장하지 못했습니다. 디스크 용량과 폴더 권한을 확인하세요.")
        st.download_button("원문 TXT", evidence, file_name="transcript.txt", mime="text/plain")
        st.download_button(
            "근거 JSON", json.dumps(record.transcript.to_dict(), ensure_ascii=False, indent=2),
            file_name="transcript.json", mime="application/json",
        )
        st.download_button(
            "검수 기록 TXT", st.session_state["notes"], file_name="review.txt", mime="text/plain",
        )
        st.caption(f"로컬 저장 위치: {record.path}")


if __name__ == "__main__":
    main()
