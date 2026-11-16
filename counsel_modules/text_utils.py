from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import re


TODAY_FOCUS_MARKERS = (
    "[오늘 상담할 것]",
    "[오늘 바로 확인할 것]",
    "[오늘 확인할 것]",
)
STANDARD_TODAY_FOCUS_MARKER = "[오늘 확인할 것]"
LEARNING_FLOW_MARKERS = (
    "[학생별 학습흐름]",
    "[학생별 방향성]",
    "[학습 방향성]",
    "[학습흐름]",
)
STANDARD_LEARNING_FLOW_MARKER = "[학생별 학습흐름]"
SUMMARY_OUTPUT_BEGIN_MARKER = "SUMMARY_BEGIN"
SUMMARY_OUTPUT_END_MARKER = "SUMMARY_END"


def is_learning_flow_marker(line: str | None) -> bool:
    stripped = str(line or "").strip()
    if stripped in LEARNING_FLOW_MARKERS:
        return True
    if stripped.startswith("[") and stripped.endswith("]"):
        stripped = stripped[1:-1].strip()
    compact = re.sub(r"[\s:：*]+", "", stripped)
    return compact in {"학생별학습흐름", "학생별방향성", "학습방향성", "학습흐름", "학습흐름방향"}


def normalize_learning_flow_markers(text: str | None) -> str:
    normalized_lines = []
    for line in str(text or "").splitlines():
        normalized_lines.append(STANDARD_LEARNING_FLOW_MARKER if is_learning_flow_marker(line) else line.rstrip())
    return "\n".join(normalized_lines).strip()


def find_today_focus_marker(text: str, *, last: bool = False) -> tuple[int, str]:
    matches = [
        (text.rfind(marker) if last else text.find(marker), marker)
        for marker in TODAY_FOCUS_MARKERS
    ]
    matches = [(index, marker) for index, marker in matches if index >= 0]
    if not matches:
        return -1, ""
    return max(matches, key=lambda item: item[0]) if last else min(matches, key=lambda item: item[0])


def extract_marked_summary_output(text: str | None) -> str:
    safe_text = str(text or "")
    response_text = safe_text
    codex_role_match = re.search(r"^[ \t]*codex[ \t]*\r?$", safe_text, flags=re.IGNORECASE | re.MULTILINE)
    if codex_role_match:
        candidate = safe_text[codex_role_match.end():].lstrip("\r\n")
        token_usage_match = re.search(
            r"^[ \t]*tokens used[ \t]*\r?$",
            candidate,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if token_usage_match:
            candidate = candidate[:token_usage_match.start()]
        if candidate.strip():
            response_text = candidate.strip()

    standalone_marker_pattern = re.compile(
        rf"^[ \t]*{re.escape(SUMMARY_OUTPUT_BEGIN_MARKER)}[ \t]*\r?\n"
        rf"(.*?)"
        rf"(?:\r?\n)?^[ \t]*{re.escape(SUMMARY_OUTPUT_END_MARKER)}[ \t]*(?=\r?$)",
        flags=re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )
    standalone_matches = [
        match.group(1).strip()
        for match in standalone_marker_pattern.finditer(response_text)
        if match.group(1).strip()
    ]
    if standalone_matches:
        # Recent Codex CLI versions echo the input prompt before the agent response.
        # Prompt prose mentions both markers inline, while the actual response uses
        # marker-only lines. Prefer the first complete response block.
        return standalone_matches[0]

    marker_pattern = re.compile(
        rf"{re.escape(SUMMARY_OUTPUT_BEGIN_MARKER)}\s*(.*?)\s*{re.escape(SUMMARY_OUTPUT_END_MARKER)}",
        flags=re.DOTALL | re.IGNORECASE,
    )
    match = marker_pattern.search(response_text)
    if not match:
        return response_text
    return match.group(1).strip()


RUNTIME_NOISE_LINE_PATTERNS = (
    re.compile(r"^\s*(?:SUCCESS|ERROR):\s+The process\b", re.IGNORECASE),
    re.compile(r"\bchild process of PID\b", re.IGNORECASE),
    re.compile(r"\bhas been terminated\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:returncode|stdout|stderr)\s*:\s*", re.IGNORECASE),
    re.compile(r"^\s*(?:invalid_reason|retry_attempted|transcript_source|first_attempt_valid|retry_valid)\s*:\s*", re.IGNORECASE),
    re.compile(r"^\s*Traceback(?:\s*\(most recent call last\))?\s*:?", re.IGNORECASE),
    re.compile(r"^\s*File\s+\"(?:[A-Za-z]:\\|/)[^\"]*\"", re.IGNORECASE),
    re.compile(r"\bsubprocess\b", re.IGNORECASE),
    re.compile(r"\btaskkill\b", re.IGNORECASE),
    re.compile(r"\bCodex command\b", re.IGNORECASE),
    re.compile(r"Codex\s*실행"),
    re.compile(r"모델\s*실행\s*명령"),
    re.compile(r"정리본\s*생성\s*:\s*실패", re.IGNORECASE),
    re.compile(r"codex\s+exec\s+실행\s+실패", re.IGNORECASE),
    re.compile(r"Codex\s+CLI\s+실행"),
    re.compile(r"^\s*(?:선택된\s*실행\s*파일|실행\s*명령|WindowsApps\s*무시)\s*:", re.IGNORECASE),
    re.compile(r"^\s*(?:고정\s*codex\.cmd\s*경로|고정\s*codex\.cmd\s*존재|shutil\.which\('codex'\)|WindowsApps\s*codex\s*감지)\s*:", re.IGNORECASE),
    re.compile(r"\bcodex(?:\.cmd)?\b.*\bexec\b", re.IGNORECASE),
    re.compile(r"powershell\.exe\s+-NoProfile", re.IGNORECASE),
    re.compile(r"(?:Console\]::OutputEncoding|\$OutputEncoding)\s*=\s*\[System\.Text\.Encoding\]::UTF8", re.IGNORECASE),
    re.compile(r"Get-Content\s+-Raw\s+-Encoding", re.IGNORECASE),
    re.compile(r"codex_input\.txt", re.IGNORECASE),
    re.compile(r"AppData\\Roaming\\npm\\codex\.cmd", re.IGNORECASE),
)


def is_runtime_noise_line(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    return any(pattern.search(stripped) for pattern in RUNTIME_NOISE_LINE_PATTERNS)


def runtime_noise_line_count(text: str | None) -> int:
    return sum(1 for line in str(text or "").splitlines() if is_runtime_noise_line(line))


def remove_runtime_noise_from_summary(text: str | None) -> str:
    marked_text = extract_marked_summary_output(text)
    kept_lines = [
        line.rstrip()
        for line in marked_text.splitlines()
        if not is_runtime_noise_line(line)
    ]
    cleaned = "\n".join(kept_lines).strip()
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def extract_today_focus(summary_text: str | None) -> str:
    if not summary_text:
        return ""
    text = str(summary_text)
    start, marker = find_today_focus_marker(text)
    if start < 0:
        return ""
    section = text[start:]
    next_section = re.search(
        r"\n\s*(?:\[[^\]]+\]|\*[^*\n]+?\*|상담\s*참고사항\s*:?\s*$|숙제\s*[:：]\s*$)",
        section[len(marker):],
        flags=re.MULTILINE,
    )
    if next_section:
        end = len(marker) + next_section.start()
        section = section[:end]
    return section.strip()


def remove_today_focus_for_google_docs(summary_text: str | None) -> str:
    text = str(summary_text or "").strip()
    if not text:
        return ""
    pattern = re.compile(
        r"\s*\[(?:오늘\s*상담할\s*것|오늘\s*바로\s*확인할\s*것|오늘\s*확인할\s*것)\].*?"
        r"(?=\n\s*\[(?:상세\s*상담\s*기록|간단\s*상담\s*기록|상담\s*정리)\]|\Z)",
        re.DOTALL,
    )
    return pattern.sub("", text, count=1).strip()


def remove_identity_lines_for_google_docs(text: str | None) -> str:
    safe_text = str(text or "")
    kept_lines = []
    for line in safe_text.splitlines():
        if re.match(r"^\s*(?:[-*]\s*)?(?:학생명?|상담일)\s*:\s*.*$", line):
            continue
        kept_lines.append(line)
    cleaned = "\n".join(kept_lines).strip()
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def remove_record_number_lines_for_google_docs(text: str | None) -> str:
    safe_text = str(text or "")
    kept_lines = []
    for line in safe_text.splitlines():
        if re.match(r"^\s*(?:[-*]\s*)?기록\s*:\s*\d+\s*$", line):
            continue
        kept_lines.append(line)
    cleaned = "\n".join(kept_lines).strip()
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def google_docs_progress_subject(subject: str | None) -> str:
    clean_subject = re.sub(r"\s+", " ", str(subject or "").strip())
    if not clean_subject:
        return ""
    primary_book = google_docs_primary_book_name(clean_subject)
    known_subjects = (
        "미적분1",
        "미적분2",
        "공수1",
        "공수2",
        "미적1",
        "미적2",
        "대수",
        "기하",
        "확통",
    )
    for known_subject in known_subjects:
        if known_subject in clean_subject:
            if primary_book:
                return f"{known_subject} {primary_book}"
            return known_subject
    return primary_book


GOOGLE_DOCS_PROGRESS_LINE_PATTERN = re.compile(
    r"^(?P<prefix>\s*(?:[-*]\s*)?)"
    r"(?P<label>진도\s*/\s*차시|수업\s*진도|오늘\s*진도|진행\s*차시|이번\s*차시|진도|차시)"
    r"(?P<subject>\s*\([^)]*\))?"
    r"(?P<separator>\s*[:：/\-]\s*|\s+|$)"
)


def add_subject_to_google_docs_progress_line(line: str, subject: str) -> str:
    if not subject:
        return line.rstrip()
    match = GOOGLE_DOCS_PROGRESS_LINE_PATTERN.match(line)
    if not match or match.group("subject"):
        return line.rstrip()
    return (
        line[: match.start("label")]
        + match.group("label")
        + f"({subject})"
        + line[match.start("separator") :]
    ).rstrip()


def google_docs_context_line(
    student_name: str | None = None,
    curriculum: str | None = None,
    book: str | None = None,
    subject: str | None = None,
    counseling_date: object | None = None,
) -> str:
    clean_name = re.sub(r"\s+", " ", str(student_name or "").strip())
    clean_curriculum = google_docs_progress_subject(curriculum or subject)
    clean_book = google_docs_primary_book_name(book)
    clean_date = ""
    if counseling_date:
        clean_date = getattr(counseling_date, "isoformat", lambda: str(counseling_date))()
        clean_date = str(clean_date).strip()
    if clean_curriculum and clean_book and clean_curriculum in clean_book:
        detail = clean_book
    else:
        detail = " ".join(part for part in [clean_curriculum, clean_book] if part).strip()
    base = ""
    if clean_name and detail:
        base = f"{clean_name} / {detail}"
    if clean_name:
        base = base or clean_name
    elif detail:
        base = detail
    if clean_date and base:
        return f"[{clean_date}] {base}"
    if clean_date:
        return f"[{clean_date}]"
    if base:
        return base
    return ""


def google_docs_progress_context(
    curriculum: str | None = None,
    book: str | None = None,
    subject: str | None = None,
    context_text: str | None = None,
) -> str:
    clean_curriculum = google_docs_progress_subject(curriculum or subject)
    clean_book = google_docs_primary_book_name(book) or google_docs_primary_book_name(context_text)
    if clean_curriculum and clean_book:
        if clean_curriculum in clean_book:
            return clean_book
        if clean_book in clean_curriculum:
            return clean_curriculum
        return f"{clean_curriculum} {clean_book}"
    return clean_curriculum or clean_book


def google_docs_primary_book_name(book: str | None = None) -> str:
    compact = re.sub(r"\s+", "", str(book or "").strip())
    lower_compact = compact.lower()
    if "apex" in lower_compact or "에이펙스" in compact:
        return "APEX"
    if "a:ble" in lower_compact or "able" in lower_compact or "에이블" in compact:
        return "A:ble"
    return ""


def is_google_docs_context_line(line: str, student_name: str | None = None) -> bool:
    clean_line = re.sub(r"\s+", " ", str(line or "").strip())
    clean_name = re.sub(r"\s+", " ", str(student_name or "").strip())
    if not clean_line:
        return False
    without_date = re.sub(r"^\[?\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\]?\s*", "", clean_line).strip()
    if clean_name and (without_date == clean_name or without_date.startswith(f"{clean_name} /")):
        return True
    if re.fullmatch(r"(?:대수|공수1|공수2|미적1|미적2|미적분1|미적분2|기하|확통)(?:\s+(?:APEX|A:ble))?", without_date):
        return True
    if not clean_name:
        return False
    return False


def google_docs_section_key(line: str) -> str:
    stripped = str(line or "").strip().strip("*").strip()
    bracket_text = stripped[1:-1].strip() if stripped.startswith("[") and stripped.endswith("]") else stripped
    bracket_text = bracket_text.rstrip(":：").strip()
    compact = re.sub(r"\s+", "", bracket_text)
    if compact in {"오늘상담할것", "오늘바로확인할것", "오늘확인할것"}:
        return "today"
    if compact in {"수업전확인", "다음수업전확인", "다음수업체크"}:
        return "today"
    if compact in {"학생별학습흐름", "학생별방향성", "학습방향성", "학습흐름", "학습흐름방향"}:
        return "learning_flow"
    if compact in {"상담참고사항", "참고사항", "중요맥락", "요청사항", "구두요청사항", "특이사항"}:
        return "notes"
    if compact in {"다음조치", "다음수업조치", "다음상담조치"}:
        return "next"
    if compact in {"숙제", "숙제:"}:
        return "homework"
    if compact in {"핵심요약", "핵심", "요약"}:
        return "core"
    if compact in {"상세상담기록", "간단상담기록", "상담정리"}:
        return "notes"
    return ""


def summary_heading_key(line: str) -> str:
    stripped = str(line or "").strip()
    if not stripped:
        return ""
    if stripped.startswith(("- ", "* ")):
        return ""
    stripped = stripped.strip("*").strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        stripped = stripped[1:-1].strip()
    stripped = stripped.rstrip(":：").strip()
    compact = re.sub(r"\s+", "", stripped)
    if compact in {"오늘상담할것", "오늘바로확인할것", "오늘확인할것"}:
        return "today"
    if compact in {"수업전확인", "다음수업전확인", "다음수업체크"}:
        return "today"
    if compact in {"학생별학습흐름", "학생별방향성", "학습방향성", "학습흐름", "학습흐름방향"}:
        return "learning_flow"
    if compact in {"상담참고사항", "참고사항", "요청사항", "구두요청사항", "특이사항", "상세상담기록", "간단상담기록", "상담정리"}:
        return "notes"
    if compact in {"다음조치", "다음수업조치", "다음상담조치"}:
        return "next"
    if compact in {"숙제", "숙제:"}:
        return "homework"
    if compact in {"핵심요약", "핵심", "요약"}:
        return "notes"
    if re.match(r"^\d+\.\s+", stripped):
        return "numbered"
    return ""


def merge_next_action_sections_into_notes(summary: str) -> str:
    lines = str(summary or "").splitlines()
    kept_lines: list[str] = []
    next_action_lines: list[str] = []
    in_next_action = False

    for line in lines:
        heading_key = summary_heading_key(line)
        if heading_key == "next":
            in_next_action = True
            continue
        if in_next_action and heading_key:
            in_next_action = False
        if in_next_action:
            next_action_lines.append(line)
            continue
        kept_lines.append(line)

    while next_action_lines and not next_action_lines[0].strip():
        next_action_lines.pop(0)
    while next_action_lines and not next_action_lines[-1].strip():
        next_action_lines.pop()
    if not next_action_lines:
        return summary.strip()

    notes_heading_index = -1
    for index, line in enumerate(kept_lines):
        if summary_heading_key(line) == "notes":
            notes_heading_index = index
            break

    if notes_heading_index < 0:
        if kept_lines and kept_lines[-1].strip():
            kept_lines.append("")
        kept_lines.extend(["*상담 참고사항*", *next_action_lines])
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()

    insert_at = len(kept_lines)
    for index in range(notes_heading_index + 1, len(kept_lines)):
        if summary_heading_key(kept_lines[index]):
            insert_at = index
            break
    insertion = next_action_lines[:]
    previous_line = kept_lines[insert_at - 1].strip() if insert_at > notes_heading_index + 1 else ""
    first_insert_line = next((line.strip() for line in insertion if line.strip()), "")
    should_separate = bool(
        previous_line
        and not (previous_line.startswith(("-", "*")) and first_insert_line.startswith(("-", "*")))
    )
    if should_separate:
        insertion = ["", *insertion]
    kept_lines[insert_at:insert_at] = insertion
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()


def normalize_core_summary_line_for_google_docs(line: str) -> str:
    match = re.match(r"^\s*(?:[-*]\s*)?(?:핵심\s*요약|핵심요약|핵심|요약)\s*[:：]\s*(.+?)\s*$", line)
    if not match:
        return line.rstrip()
    content = match.group(1).strip()
    return f"- {content}" if content else ""


def format_google_docs_progress_line(line: str, subject: str) -> str:
    match = GOOGLE_DOCS_PROGRESS_LINE_PATTERN.match(line)
    if not match:
        candidate = re.sub(r"^\s*[-*]\s*", "", line.strip(), count=1)
        match = GOOGLE_DOCS_PROGRESS_LINE_PATTERN.match(candidate)
        if not match:
            return line.rstrip()
        line = candidate
    existing_subject = ""
    subject_group = match.group("subject") or ""
    if subject_group:
        existing_subject = subject_group.strip()[1:-1].strip()
    final_subject = subject or existing_subject
    if subject and existing_subject:
        subject_key = re.sub(r"\s+", "", subject)
        existing_key = re.sub(r"\s+", "", existing_subject)
        if subject_key and subject_key in existing_key:
            final_subject = existing_subject
        elif existing_key and existing_key in subject_key:
            final_subject = subject
    value = line[match.end("separator") :].strip()
    label = f"진도({final_subject}):" if final_subject else "진도:"
    return f"{label} {value}".rstrip()


def ensure_google_docs_today_heading(lines: list[str]) -> list[str]:
    first_content = next((line.strip() for line in lines if line.strip()), "")
    if not first_content:
        return lines
    normalized = re.sub(r"\s+", "", first_content.strip("-* "))
    if normalized in {"숙제체크", "학습습관/태도체크", "학생상태/생활맥락", "상담참고사항"}:
        return lines
    if first_content.startswith(("-", "*")):
        return ["숙제 체크", *lines]
    return lines


def google_docs_homework_compare_key(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return re.sub(r"[\s\.,，。．;；:：!！?？\"'“”‘’()\[\]{}<>]+", "", normalized)


def google_docs_homework_book_name(value: str | None) -> str:
    raw = re.sub(r"\s+", " ", str(value or "").strip())
    if not raw:
        return ""
    primary_book = google_docs_primary_book_name(raw)
    if primary_book:
        return primary_book
    compact = re.sub(r"\s+", "", raw)
    if "트레이닝팩" in compact or compact == "트팩":
        return "트레이닝팩"
    if "올포유" in compact:
        return "올포유"
    if "라이트센" in compact:
        return "라이트센"
    return raw


def split_google_docs_homework_books(value: str | None) -> list[str]:
    raw = re.sub(r"\s+", " ", str(value or "").strip())
    if not raw:
        return []
    parts = re.split(r"\s*(?:,|，|、|/|\+|&|및|그리고)\s*", raw)
    books: list[str] = []
    seen: set[str] = set()
    for part in parts:
        book = google_docs_homework_book_name(part)
        key = google_docs_homework_compare_key(book)
        if book and key not in seen:
            seen.add(key)
            books.append(book)
    return books


def parse_google_docs_homework_line(line: str | None) -> tuple[str, str]:
    text = re.sub(r"^\s*[-*]\s*", "", str(line or "").strip(), count=1).strip()
    if not text:
        return "", ""
    match = re.match(r"^([^:：]{1,40})\s*[:：]\s*(.+?)\s*$", text)
    if not match:
        return "", text
    label = re.sub(r"\s+", "", match.group(1).strip())
    return label, match.group(2).strip()


def normalize_google_docs_homework_lines(lines: list[str]) -> list[str]:
    books: list[str] = []
    book_keys: set[str] = set()
    current_books: list[str] = []
    book_assignments: list[tuple[str, str]] = []
    book_assignment_keys: set[tuple[str, str]] = set()
    generic_assignments: list[str] = []
    generic_assignment_keys: set[str] = set()
    detail_lines: list[str] = []
    detail_keys: set[str] = set()

    def add_book(book: str) -> None:
        key = google_docs_homework_compare_key(book)
        if book and key not in book_keys:
            book_keys.add(key)
            books.append(book)

    def add_generic_assignment(assignment: str) -> None:
        key = google_docs_homework_compare_key(assignment)
        if assignment and key not in generic_assignment_keys:
            generic_assignment_keys.add(key)
            generic_assignments.append(assignment)

    def add_detail_line(line: str) -> None:
        key = google_docs_homework_compare_key(line)
        if line and key not in detail_keys:
            detail_keys.add(key)
            detail_lines.append(f"- {line}")

    for line in lines:
        label, value = parse_google_docs_homework_line(line)
        if not label and not value:
            continue
        if label in {"교재", "교재명", "사용교재"}:
            current_books = split_google_docs_homework_books(value)
            for book in current_books:
                add_book(book)
            continue
        if label in {"다음숙제", "다음과제", "숙제", "과제"}:
            if current_books:
                for book in current_books:
                    key = (google_docs_homework_compare_key(book), google_docs_homework_compare_key(value))
                    if value and key not in book_assignment_keys:
                        book_assignment_keys.add(key)
                        book_assignments.append((book, value))
            else:
                add_generic_assignment(value)
            continue

        book_label = google_docs_homework_book_name(label)
        if book_label in {"APEX", "A:ble", "트레이닝팩", "올포유", "라이트센"}:
            add_book(book_label)
            current_books = [book_label]
            key = (google_docs_homework_compare_key(book_label), google_docs_homework_compare_key(value))
            if value and key not in book_assignment_keys:
                book_assignment_keys.add(key)
                book_assignments.append((book_label, value))
            continue

        text = f"{label}: {value}" if label else value
        add_detail_line(text)

    output: list[str] = []
    assignment_keys = {google_docs_homework_compare_key(assignment) for _, assignment in book_assignments}
    if book_assignments and len(assignment_keys) == 1 and not detail_lines:
        for book, _ in book_assignments:
            add_book(book)
        if books:
            output.append(f"- 교재: {', '.join(books)}")
        output.extend(detail_lines)
        output.append(f"- 다음 숙제: {book_assignments[0][1]}")
    elif book_assignments:
        output.extend(detail_lines)
        for book, assignment in book_assignments:
            output.append(f"- {book}: {assignment}")
    else:
        if books:
            output.append(f"- 교재: {', '.join(books)}")
        output.extend(detail_lines)

    for assignment in generic_assignments:
        if google_docs_homework_compare_key(assignment) not in assignment_keys:
            output.append(f"- 다음 숙제: {assignment}")

    return output


def google_docs_progress_from_homework_value(value: str | None) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    match = re.search(r"(진행률\s*[:：]?\s*[^,，]+)", text)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def remove_google_docs_progress_from_homework_value(value: str | None) -> str:
    parts = [part.strip() for part in re.split(r"\s*[,，]\s*", str(value or "").strip())]
    kept_parts = [
        part
        for part in parts
        if part and not re.match(r"^진행률\s*[:：]?", part)
    ]
    return ", ".join(kept_parts).strip()


def lift_primary_book_progress_from_homework(
    homework_lines: list[str],
    progress_subject: str,
) -> tuple[str, list[str]]:
    primary_book = google_docs_primary_book_name(progress_subject)
    if not primary_book:
        return "", homework_lines

    progress_value = ""
    for line in homework_lines:
        label, value = parse_google_docs_homework_line(line)
        if google_docs_homework_book_name(label) != primary_book:
            continue
        progress_value = google_docs_progress_from_homework_value(value)
        if progress_value:
            break
    if not progress_value:
        return "", homework_lines

    cleaned_lines: list[str] = []
    for line in homework_lines:
        label, value = parse_google_docs_homework_line(line)
        if label and google_docs_progress_from_homework_value(value):
            cleaned_value = remove_google_docs_progress_from_homework_value(value)
            if cleaned_value:
                cleaned_lines.append(f"- {label}: {cleaned_value}")
            continue
        if label in {"진행률"}:
            continue
        cleaned_lines.append(line)

    progress_line = format_google_docs_progress_line(f"진도: {progress_value}", progress_subject)
    return progress_line, cleaned_lines


def prepare_summary_for_google_docs_cell(
    text: str | None,
    subject: str | None = None,
    student_name: str | None = None,
    curriculum: str | None = None,
    book: str | None = None,
    counseling_date: object | None = None,
) -> str:
    cleaned = remove_runtime_noise_from_summary(text)
    cleaned = remove_identity_lines_for_google_docs(cleaned)
    cleaned = remove_record_number_lines_for_google_docs(cleaned)
    cleaned = remove_meta_uncertainty_notes(cleaned)
    if not cleaned:
        return ""

    progress_pattern = re.compile(
        r"^(?:진도\s*/\s*차시|수업\s*진도|오늘\s*진도|진행\s*차시|이번\s*차시|진도|차시)"
        r"(?:\s*\([^)]*\))?"
        r"(?:\s*[:：/\-]\s*|\s+|$)"
    )
    progress_subject = google_docs_progress_context(
        curriculum=curriculum or subject,
        book=book,
        context_text=cleaned,
    )
    sections: dict[str, list[str]] = {
        "attendance": [],
        "body": [],
        "learning_flow": [],
        "today": [],
        "notes": [],
        "next": [],
        "homework": [],
    }
    current_section = "body"
    for line in cleaned.splitlines():
        stripped = line.strip()
        if re.match(r"^출결\s*[:：]\s*\S", stripped):
            sections["attendance"].append(stripped)
            continue
        if is_google_docs_context_line(stripped, student_name):
            continue
        if current_section in {"body", "today", "notes"} and is_meta_uncertainty_line(line):
            continue
        section_key = google_docs_section_key(stripped)
        if section_key:
            current_section = "notes" if section_key in {"core", "next"} else section_key
            continue
        core_summary_line = normalize_core_summary_line_for_google_docs(line)
        if core_summary_line != line.rstrip():
            if core_summary_line:
                sections["notes"].append(core_summary_line)
            continue
        candidate = re.sub(r"^\s*[-*]\s*", "", stripped, count=1)
        if progress_pattern.match(candidate):
            sections.setdefault("progress", []).append(format_google_docs_progress_line(line.rstrip(), progress_subject))
        else:
            sections[current_section].append(line.rstrip())

    if sections["body"]:
        if sections["notes"] and sections["body"][-1].strip():
            sections["body"].append("")
        sections["notes"] = sections["body"] + sections["notes"]

    rendered_parts: list[str] = []
    if sections["attendance"]:
        rendered_parts.append("\n".join(dict.fromkeys(sections["attendance"])))
    homework_lines: list[str] = []
    if any(line.strip() for line in sections["homework"]):
        homework_lines = normalize_google_docs_homework_lines(sections["homework"])

    progress_text = "\n".join(line for line in sections.get("progress", []) if line.strip()).strip()
    if not progress_text and homework_lines:
        lifted_progress_text, homework_lines = lift_primary_book_progress_from_homework(
            homework_lines,
            progress_subject,
        )
        progress_text = lifted_progress_text.strip()
    if progress_text:
        rendered_parts.append(progress_text)
    learning_flow_lines = clean_notes_section_lines(sections.get("learning_flow", []))
    if learning_flow_lines:
        rendered_parts.append("[학생별 학습흐름]\n" + "\n".join(learning_flow_lines).strip())
    if any(line.strip() for line in sections["today"]):
        today_lines = ensure_google_docs_today_heading(sections["today"])
        rendered_parts.append("[오늘 확인할 것]\n" + "\n".join(today_lines).strip())
    notes_lines = clean_notes_section_lines(sections["notes"])
    if notes_lines:
        rendered_parts.append("상담 참고사항\n" + "\n".join(notes_lines).strip())
    if any(line.strip() for line in homework_lines):
        rendered_parts.append("숙제:\n" + "\n".join(homework_lines).strip())

    prepared = "\n\n".join(part for part in rendered_parts if part.strip())
    return re.sub(r"\n{3,}", "\n\n", prepared).strip()


def split_transcript_sentences(transcript_text: str | None) -> list[str]:
    safe_transcript = transcript_text or ""
    normalized = re.sub(r"\s+", " ", safe_transcript.strip())
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?。！？]|[.?!])\s+|(?<=[다요죠까])\.\s*|\n+", normalized)
    sentences = [part.strip(" .!?。！？") for part in parts if part.strip(" .!?。！？")]
    if len(sentences) <= 1:
        sentences = [part.strip() for part in re.split(r"[.!?\n]+", normalized) if part.strip()]
    return sentences


def format_transcript_file(student_name: str, counseling_date: date, transcript: str) -> str:
    safe_transcript = transcript or ""
    return (
        "[상담 정보]\n"
        f"학생명: {student_name.strip()}\n"
        f"상담일: {counseling_date.isoformat()}\n\n"
        "[음성 인식 원문]\n"
        f"{safe_transcript.strip()}\n"
    )


def normalize_summary_identity(summary: str, student_name: str, counseling_date: date) -> str:
    del student_name, counseling_date
    return remove_identity_lines_for_google_docs(summary)


def format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes:02d}분 {secs:02d}초"


def extract_student_name_from_audio_filename(file_name: str | Path | None) -> str | None:
    if not file_name:
        return None
    stem = Path(str(file_name)).stem
    cleaned = re.sub(r"\s+", " ", stem).strip()
    cleaned = re.sub(r"_[2-9]\d{0,2}$", "", cleaned).strip()
    return cleaned or None


def parse_draft_counseling_date(value: object, fallback: date) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        clean_value = value.strip()
        if clean_value:
            try:
                return date.fromisoformat(clean_value)
            except ValueError:
                try:
                    return datetime.fromisoformat(clean_value).date()
                except ValueError:
                    pass
    return fallback


def safe_date_for_filename(counseling_date: date) -> str:
    return counseling_date.isoformat().replace("/", "-")


def sanitize_filename_part(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value.strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = cleaned.strip("._")
    return cleaned or "unknown"


def section_boundary_for_reorder(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    if summary_heading_key(stripped):
        return True
    if re.match(r"^\[[^\]]+\]\s*$", stripped):
        return True
    if re.match(r"^\*[^*].+?\*\s*$", stripped):
        return True
    if re.match(r"^숙제\s*[:：]\s*$", stripped):
        return True
    if GOOGLE_DOCS_PROGRESS_LINE_PATTERN.match(stripped):
        return True
    return False


def pop_summary_section(lines: list[str], start_index: int) -> tuple[list[str], list[str]]:
    end_index = start_index + 1
    while end_index < len(lines) and not section_boundary_for_reorder(lines[end_index]):
        end_index += 1
    section = lines[start_index:end_index]
    remaining = lines[:start_index] + lines[end_index:]
    return section, remaining


def move_today_check_section_to_top(summary: str) -> str:
    normalized = normalize_learning_flow_markers(summary).strip()
    if not normalized:
        return ""
    lines = normalized.splitlines()
    learning_section: list[str] = []
    today_section: list[str] = []

    learning_index = next((index for index, line in enumerate(lines) if is_learning_flow_marker(line)), -1)
    if learning_index >= 0:
        learning_section, lines = pop_summary_section(lines, learning_index)

    today_index = next((index for index, line in enumerate(lines) if line.strip() in TODAY_FOCUS_MARKERS), -1)
    if today_index >= 0:
        today_section, lines = pop_summary_section(lines, today_index)

    learning = "\n".join(line.rstrip() for line in learning_section).strip()
    today = "\n".join(line.rstrip() for line in today_section).strip()
    rest = "\n".join(line.rstrip() for line in lines).strip()
    return "\n\n".join(part for part in [learning, today, rest] if part).strip()


def extract_learning_flow_section(summary_text: str | None) -> str:
    lines = normalize_learning_flow_markers(summary_text).splitlines()
    start_index = next((index for index, line in enumerate(lines) if is_learning_flow_marker(line)), -1)
    if start_index < 0:
        return ""
    section, _remaining = pop_summary_section(lines, start_index)
    return "\n".join(line.rstrip() for line in section).strip()


def normalize_legacy_check_sections(summary: str) -> str:
    lines = summary.splitlines()
    kept_lines = []
    legacy_check_lines = []
    in_legacy_section = False
    seen_check_bullets = set()

    def add_check_line(value: str) -> None:
        clean = value.strip()
        if not clean or clean == "-":
            return
        bullet_text = re.sub(r"^\s*[-*]\s*", "", clean).strip()
        if not bullet_text or bullet_text in seen_check_bullets:
            return
        if not has_specific_today_focus_item(bullet_text):
            return
        seen_check_bullets.add(bullet_text)
        legacy_check_lines.append(f"- {bullet_text}")

    for line in lines:
        stripped = line.strip()
        is_legacy_heading = (
            stripped == "[다음 상담 때 확인]"
            or stripped == "[확인 필요]"
            or bool(re.match(r"^\d+\.\s*확인\s*필요$", stripped))
        )
        is_numbered_heading = bool(re.match(r"^\d+\.\s+", stripped))
        is_bracket_heading = bool(re.match(r"^\[[^\]]+\]$", stripped))

        if is_legacy_heading:
            in_legacy_section = True
            continue

        if in_legacy_section and (is_numbered_heading or is_bracket_heading):
            in_legacy_section = False

        if in_legacy_section:
            add_check_line(line)
            continue

        kept_lines.append(line)

    cleaned = "\n".join(kept_lines)
    cleaned = re.sub(r"(?m)^\s*\d+\.\s*확인\s*필요\s*\n(?:\s*[-*].*\n?)*", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*\[확인 필요\]\s*\n(?:\s*[-*].*\n?)*", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*\[다음 상담 때 확인\]\s*\n(?:\s*[-*].*\n?)*", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not any(marker in cleaned for marker in TODAY_FOCUS_MARKERS):
        if not legacy_check_lines:
            return cleaned
        check_lines = "\n".join(legacy_check_lines)
        prefix = (
            f"{STANDARD_TODAY_FOCUS_MARKER}\n\n"
            "수업 전 확인"
            f"{chr(10) + check_lines if check_lines else ''}"
        )
        return "\n\n".join(part for part in [prefix, cleaned] if part).strip()
    next_check_match = re.search(r"(?m)^(?:다음 수업 체크|숙제 체크):?\s*$", cleaned)
    if legacy_check_lines and next_check_match:
        insert_at = next_check_match.end()
        before = cleaned[:insert_at].rstrip()
        after = cleaned[insert_at:].lstrip()
        existing_checks = {
            re.sub(r"^\s*[-*]\s*", "", line.strip()).strip()
            for line in after.splitlines()
            if line.strip().startswith("-")
        }
        merged = [
            line for line in legacy_check_lines
            if re.sub(r"^\s*[-*]\s*", "", line).strip() not in existing_checks
        ]
        if merged:
            cleaned = f"{before}\n" + "\n".join(merged) + ("\n" + after if after else "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def normalize_today_focus_line_text(line: str) -> str:
    clean = re.sub(r"^\s*[-*]\s*", "", str(line or "")).strip()
    clean = clean.strip("[](){}<>")
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


def has_today_focus_concrete_object(line: str) -> bool:
    text = normalize_today_focus_line_text(line)
    if not text:
        return False
    concrete_patterns = (
        r"\b(?:APEX|A:ble)\b",
        r"(?:트레이닝팩|마플|개념원리|단원평가|오답노트|프린트|노트|파일|사진|자료명|백지리뷰)",
        r"p\.\s*\d+(?:\s*[~-]\s*\d+)?",
        r"\d+\s*(?:차시|쪽|페이지|번|회|문항|장)",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in concrete_patterns)


def has_today_focus_check_evidence(line: str) -> bool:
    text = normalize_today_focus_line_text(line)
    if not text:
        return False
    evidence_patterns = (
        r"풀이\s*흔적",
        r"오답\s*재풀이",
        r"백지\s*리뷰\s*제출",
        r"백지리뷰\s*제출",
        r"프린트\s*\d*\s*장?\s*.*제출",
        r"노트\s*확인",
        r"오답\s*노트",
        r"오답노트",
        r"파일\s*제출",
        r"사진\s*제출",
        r"제출\s*여부",
        r"영상\s*(?:시청|청취)\s*(?:여부\s*)?확인",
        r"확인",
    )
    return any(re.search(pattern, text) for pattern in evidence_patterns)


def is_homework_status_only_check(line: str) -> bool:
    text = normalize_today_focus_line_text(line)
    if not text:
        return False
    status_patterns = (
        r"숙제.*(?:진행|완료)\s*여부\s*확인",
        r"숙제.*(?:했는지|해왔는지|해\s*왔는지)\s*확인",
        r"숙제\s*풀이\s*흔적\s*확인",
        r"(?:이전|지난|정한)\s*숙제.*(?:풀이\s*흔적|했는지|해왔는지|해\s*왔는지).*확인",
        r"리뷰\s*완료\s*여부\s*확인",
    )
    return any(re.search(pattern, text) for pattern in status_patterns)


def has_concrete_check_target(line: str) -> bool:
    text = normalize_today_focus_line_text(line)
    if not text:
        return False
    if is_homework_status_only_check(text):
        return False
    return has_today_focus_concrete_object(text) and has_today_focus_check_evidence(text)


def has_actionable_check_target(line: str) -> bool:
    text = normalize_today_focus_line_text(line)
    if not text:
        return False
    if has_concrete_check_target(text):
        return True
    actionable_patterns = (
        r"자료명\s*확인",
        r"가져온\s*(?:자료|프린트).*(?:자료명|범위|풀이|확인)",
        r"프린트.*(?:남은\s*)?(?:풀이\s*)?범위\s*확인",
    )
    return any(re.search(pattern, text) for pattern in actionable_patterns)


def has_specific_today_focus_item(line: str) -> bool:
    text = normalize_today_focus_line_text(line)
    if not text or is_generic_today_focus_line(text) or is_meta_uncertainty_line(text):
        return False
    if has_actionable_check_target(text):
        return True
    specific_patterns = (
        r"발표.*(?:준비|진행|시간|확인)",
        r"시험\s*기간.*(?:시간|확보|준비)",
        r"진로.*(?:관심|분야|활동|연결)",
        r"(?:자료|프린트).*(?:자료명|남은|범위|풀이)",
        r"(?:시야|안경|칠판|화면).*(?:불편|보기|확인)",
        r"(?:APEX|A:ble|트레이닝팩|단원평가|오답노트|프린트)\s*(?:\d+\s*차시|p\.\s*\d+|\d+\s*회|\d+\s*번|남은|제출|풀이|오답|진행)",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in specific_patterns)


def is_today_focus_subheading(line: str) -> bool:
    clean = normalize_today_focus_line_text(line)
    compact = re.sub(r"[\s:：]+", "", clean)
    return compact in {
        "수업전확인",
        "다음수업전확인",
        "오늘확인할것",
        "확인할항목",
        "체크할것",
        "숙제",
        "요청사항",
        "특이사항",
        "다음수업체크",
    }


def is_generic_today_focus_line(line: str) -> bool:
    text = normalize_today_focus_line_text(line)
    if not text:
        return False
    compact = re.sub(r"[\s:：,，.。!！?？~\-]+", "", text)

    if compact in {
        "숙제체크",
        "숙제확인",
        "숙제진행확인",
        "숙제진행여부확인",
        "숙제완료여부확인",
        "정한숙제범위확인",
        "범위확인",
        "완료여부확인",
        "리뷰완료여부확인",
        "영상시청여부확인",
        "이해도확인",
        "집중도확인",
        "태도확인",
        "학습의지확인",
        "숙제",
        "요청사항",
        "특이사항",
        "다음수업체크",
        "기존확인",
    }:
        return True

    if has_concrete_check_target(text):
        return False

    generic_patterns = (
        r"(?:이전|지난|정한)?\s*숙제.*(?:실제로\s*)?(?:해\s*왔는지|해왔는지|했는지|풀이\s*흔적).*확인",
        r"숙제\s*풀이\s*흔적\s*확인",
        r"수업\s*영상\s*(?:청취|시청).*확인",
        r"영상\s*(?:들었는지|청취|시청).*확인",
        r"(?:월요일|화요일|수요일|목요일|금요일|토요일|일요일|다음\s*시간|이번\s*주|다음\s*수업)?\s*까지?\s*정한\s*(?:숙제\s*)?범위.*어디까지.*(?:풀|해).*확인",
        r"(?:숙제\s*)?범위.*어디까지.*(?:풀|해).*확인",
        r"어디까지\s*(?:풀|해)(?:어)?왔는지\s*확인",
        r"정한\s*(?:숙제\s*)?범위\s*확인",
        r"(?:숙제|리뷰|영상)\s*(?:진행|완료|시청)\s*여부\s*확인",
        r"숙제.*완료\s*여부\s*확인",
        r"(?:이해도|집중도|태도|학습\s*의지)\s*확인",
    )
    return any(re.search(pattern, text) for pattern in generic_patterns)


def is_notes_section_marker(line: str) -> bool:
    stripped = str(line or "").strip()
    compact = re.sub(r"[\s:：*]+", "", stripped)
    return compact == "상담참고사항"


def is_summary_spacing_section_heading(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    if stripped in TODAY_FOCUS_MARKERS:
        return True
    if is_notes_section_marker(stripped):
        return True
    if re.match(r"^숙제\s*[:：]\s*$", stripped):
        return True
    if re.match(r"^\[[^\]]+\]\s*$", stripped):
        return True
    if re.match(r"^\*[^*\n]+?\*\s*$", stripped):
        return True
    return False


def format_summary_section_spacing(text: str | None) -> str:
    raw_lines = str(text or "").splitlines()
    lines = [line.strip() for line in raw_lines if line.strip()]
    if not lines:
        return ""

    result: list[str] = []

    def append_blank() -> None:
        if result and result[-1] != "":
            result.append("")

    for line in lines:
        if line in TODAY_FOCUS_MARKERS:
            if result:
                append_blank()
            result.append(line)
            append_blank()
            continue

        if result and is_summary_spacing_section_heading(line):
            append_blank()

        result.append(line)

    while result and result[0] == "":
        result.pop(0)
    while result and result[-1] == "":
        result.pop()

    compacted: list[str] = []
    previous_blank = False
    for line in result:
        is_blank = line == ""
        if is_blank and previous_blank:
            continue
        compacted.append(line)
        previous_blank = is_blank
    return "\n".join(compacted).strip()


def meta_uncertainty_section_boundary(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    if stripped in TODAY_FOCUS_MARKERS or is_notes_section_marker(stripped):
        return True
    if re.match(r"^\[[^\]]+\]\s*$", stripped):
        return True
    if re.match(r"^\*[^*].+?\*\s*$", stripped):
        return True
    if re.match(r"^숙제\s*[:：]\s*$", stripped):
        return True
    if GOOGLE_DOCS_PROGRESS_LINE_PATTERN.match(stripped):
        return True
    compact = re.sub(r"[\s:：*]+", "", stripped)
    return compact in {"숙제", "상세상담기록", "간단상담기록", "상담정리"}


def is_meta_uncertainty_line(line: str) -> bool:
    text = normalize_today_focus_line_text(line)
    if not text:
        return False
    if has_actionable_check_target(text):
        return False
    meta_patterns = (
        r"원문상",
        r"명확하지\s*않",
        r"확인되지\s*(?:않|않는다|않음|않았습니다)?",
        r"(?:로만|것으로만)\s*확인",
        r"발화가\s*있어",
        r"발화상",
        r"원문에는",
        r"직접\s*언급되지\s*않",
        r"추정(?:됨|된다)?",
        r"보임",
        r"보인다",
        r"가능성이\s*있",
        r"것으로\s*(?:판단|보)",
        r"확인\s*필요해\s*보",
    )
    return any(re.search(pattern, text) for pattern in meta_patterns)


EMPTY_NOTES_PLACEHOLDER_VALUES = {
    "",
    ".",
    "-",
    "없음",
    "해당없음",
    "특이사항없음",
    "상담참고사항없음",
    "확인된내용없음",
    "별도내용없음",
    "내용없음",
    "n/a",
    "na",
    "null",
    "none",
}


def is_empty_notes_placeholder_line(line: str) -> bool:
    text = str(line or "").strip()
    text = re.sub(r"^\s*[-*]\s*", "", text).strip()
    text = text.strip("[](){} ")
    text = re.sub(r"[.。!！?？~\-]+$", "", text).strip()
    compact = re.sub(r"[\s:：,，]+", "", text).lower()
    return compact in EMPTY_NOTES_PLACEHOLDER_VALUES


def clean_notes_section_lines(lines: list[str]) -> list[str]:
    kept = [line.rstrip() for line in lines if line.strip() and not is_empty_notes_placeholder_line(line)]
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return kept


def remove_empty_notes_sections(text: str) -> str:
    lines = str(text or "").splitlines()
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not is_notes_section_marker(line):
            result.append(line.rstrip())
            index += 1
            continue

        marker = line.rstrip()
        section_lines: list[str] = []
        index += 1
        while index < len(lines) and not meta_uncertainty_section_boundary(lines[index]):
            section_lines.append(lines[index].rstrip())
            index += 1

        while section_lines and not section_lines[0].strip():
            section_lines.pop(0)
        while section_lines and not section_lines[-1].strip():
            section_lines.pop()
        section_lines = clean_notes_section_lines(section_lines)
        if section_lines:
            result.append(marker)
            result.extend(section_lines)

    cleaned = "\n".join(result)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def remove_empty_today_focus_sections(text: str) -> str:
    lines = str(text or "").splitlines()
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped not in TODAY_FOCUS_MARKERS:
            result.append(line.rstrip())
            index += 1
            continue

        marker = stripped
        section_lines: list[str] = []
        index += 1
        while index < len(lines) and not today_focus_section_boundary(lines[index]):
            section_lines.append(lines[index].rstrip())
            index += 1

        while section_lines and not section_lines[0].strip():
            section_lines.pop(0)
        while section_lines and not section_lines[-1].strip():
            section_lines.pop()
        substantive = [
            line for line in section_lines
            if line.strip() and not is_today_focus_subheading(line)
        ]
        if substantive:
            result.append(marker)
            result.append("")
            result.extend(section_lines)

    cleaned = "\n".join(result)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def remove_meta_uncertainty_notes(text: str) -> str:
    if not text:
        return ""

    lines = str(text).splitlines()
    result: list[str] = []
    current_section = ""
    for line in lines:
        stripped = line.strip()
        if stripped in TODAY_FOCUS_MARKERS:
            current_section = "today"
            result.append(line.rstrip())
            continue
        if is_notes_section_marker(stripped):
            current_section = "notes"
            result.append(line.rstrip())
            continue
        if meta_uncertainty_section_boundary(stripped):
            current_section = ""
            result.append(line.rstrip())
            continue
        if current_section in {"today", "notes"} and is_meta_uncertainty_line(line):
            continue
        result.append(line.rstrip())

    cleaned = "\n".join(result)
    cleaned = remove_empty_today_focus_sections(cleaned)
    cleaned = remove_empty_notes_sections(cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def today_focus_section_boundary(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    heading_key = summary_heading_key(stripped)
    if heading_key and heading_key != "today":
        return True
    if GOOGLE_DOCS_PROGRESS_LINE_PATTERN.match(stripped):
        return True
    if re.match(r"^\[[^\]]+\]\s*$", stripped):
        return True
    if re.match(r"^\*[^*].+?\*\s*$", stripped):
        return True
    if re.match(r"^숙제\s*[:：]\s*$", stripped):
        return True
    compact = re.sub(r"[\s:：*]+", "", stripped)
    return compact in {"상담참고사항", "상세상담기록", "간단상담기록", "상담정리"}


def clean_today_focus_section_lines(lines: list[str]) -> list[str]:
    kept: list[str] = []
    for line in lines:
        if is_generic_today_focus_line(line):
            continue
        kept.append(line.rstrip())

    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()

    substantive = [
        line for line in kept
        if line.strip() and not is_today_focus_subheading(line)
    ]
    if not substantive:
        return []

    cleaned: list[str] = []
    previous_blank = False
    for line in kept:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        cleaned.append(line)
        previous_blank = is_blank
    return cleaned


def summary_today_focus_items(summary: str | None) -> list[str]:
    focus = extract_today_focus(summary)
    if not focus:
        return []
    items: list[str] = []
    for line in focus.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("-", "*")):
            continue
        if has_specific_today_focus_item(stripped):
            item = re.sub(r"^\s*[-*]\s*", "", stripped).strip()
            if item and item not in items:
                items.append(item)
    return items


def summary_has_required_today_focus(summary: str | None) -> bool:
    return bool(summary_today_focus_items(summary))


def summary_notes_items(summary: str | None) -> list[str]:
    lines = str(summary or "").splitlines()
    items: list[str] = []
    in_notes = False
    for line in lines:
        stripped = line.strip()
        if is_notes_section_marker(stripped):
            in_notes = True
            continue
        if in_notes and meta_uncertainty_section_boundary(stripped):
            break
        if not in_notes:
            continue
        if not stripped.startswith(("-", "*")):
            continue
        item = re.sub(r"^\s*[-*]\s*", "", stripped).strip()
        if is_empty_notes_placeholder_line(item):
            continue
        if not item or is_meta_uncertainty_line(item):
            continue
        if item not in items:
            items.append(item)
    return items


def summary_has_required_notes(summary: str | None) -> bool:
    return bool(summary_notes_items(summary))


def is_meaningful_summary_content_line(line: str | None) -> bool:
    text = normalize_today_focus_line_text(line)
    if not text:
        return False
    if is_runtime_noise_line(text):
        return False
    if is_meta_uncertainty_line(text):
        return False
    if is_generic_today_focus_line(text):
        return False
    if is_empty_notes_placeholder_line(text):
        return False
    if text in TODAY_FOCUS_MARKERS or is_today_focus_subheading(text) or is_notes_section_marker(text):
        return False
    if re.match(r"^\[[^\]]+\]\s*$", text):
        return False
    if re.match(r"^\*[^*].+?\*\s*$", text):
        return False
    if re.match(r"^(?:진도|숙제|상담 참고사항)\s*[:：]?\s*$", text):
        return False

    item = re.sub(r"^\s*[-*]\s*", "", text).strip()
    if not item or len(item) <= 2:
        return False
    if is_empty_notes_placeholder_line(item):
        return False
    if is_meta_uncertainty_line(item):
        return False
    if is_runtime_noise_line(item):
        return False
    if re.fullmatch(
        r"(?:없음|해당\s*없음|특이사항\s*없음|상담\s*참고사항\s*없음|확인된\s*내용\s*없음|별도\s*내용\s*없음|내용\s*없음|N/?A|null|none|None|\.)",
        item,
        flags=re.IGNORECASE,
    ):
        return False
    return True


def summary_has_meaningful_body_content(summary: str | None) -> bool:
    text = remove_runtime_noise_from_summary(summary)
    if not text:
        return False
    cleaned = remove_empty_placeholder_lines(text)
    cleaned = remove_meta_uncertainty_notes(cleaned)
    cleaned = remove_empty_notes_sections(remove_empty_today_focus_sections(cleaned))
    if not cleaned.strip():
        return False
    if summary_has_required_today_focus(cleaned):
        return True
    if summary_has_required_notes(cleaned):
        return True
    return any(is_meaningful_summary_content_line(line) for line in cleaned.splitlines())


def homework_lines_for_today_focus(summary: str | None) -> list[str]:
    lines = str(summary or "").splitlines()
    homework_lines: list[str] = []
    in_homework = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^숙제\s*[:：]\s*$", stripped):
            in_homework = True
            continue
        if in_homework and today_focus_section_boundary(stripped):
            break
        if in_homework and stripped:
            homework_lines.append(stripped)
    return homework_lines


def build_today_focus_items_from_homework(summary: str | None) -> list[str]:
    items: list[str] = []

    def add_item(value: str) -> None:
        clean = normalize_today_focus_line_text(value)
        if not clean or is_generic_today_focus_line(clean) or is_meta_uncertainty_line(clean):
            return
        if has_specific_today_focus_item(clean) and clean not in items:
            items.append(clean)

    book_names = r"APEX|A:ble|트레이닝팩|마플|개념원리|단원평가|오답노트|프린트|노트|파일|사진"
    range_pattern = re.compile(
        r"(p\.\s*\d+(?:\s*[~-]\s*\d+)?|"
        r"\d+\s*차시(?:\s*\([^)]*\))?|"
        r"\d+\s*쪽(?:\s*[~-]\s*\d+\s*쪽?)?|"
        r"\d+\s*페이지|"
        r"\d+\s*회(?:\s*\d+\s*번)?|"
        r"\d+\s*번(?:\s*,\s*\d+\s*번)*|"
        r"\d+\s*문항|"
        r"\d+\s*장)",
        re.IGNORECASE,
    )

    for raw_line in homework_lines_for_today_focus(summary):
        line = normalize_today_focus_line_text(raw_line)
        if not line:
            continue
        explicit_book = re.match(rf"^\s*({book_names})\s*[:：]\s*(.+)$", line, flags=re.IGNORECASE)
        generic_book = re.match(r"^\s*([^:：/]+?)\s*[:：]\s*(.+)$", line)
        if explicit_book:
            book = explicit_book.group(1).strip()
            detail = explicit_book.group(2).strip()
        elif generic_book:
            book = generic_book.group(1).strip()
            detail = generic_book.group(2).strip()
        else:
            book = ""
            detail = line

        if not book or re.fullmatch(r"(?:확인\s*필요|없음|해당\s*없음)", detail):
            continue

        range_match = range_pattern.search(detail)
        if not range_match:
            continue
        target = re.sub(r"\s+", "", range_match.group(1))
        if re.search(r"영상|시청|청취|듣", detail):
            add_item(f"{book} {target} 영상 시청 여부 확인")
        elif re.search(r"백지\s*리뷰|백지리뷰", detail):
            add_item(f"{book} {target} 백지리뷰 제출 여부 확인")
        elif re.search(r"오답", detail):
            add_item(f"{book} {target} 오답 재풀이 확인")
        else:
            add_item(f"{book} {target} 풀이 흔적 확인")

    return items


def build_today_focus_items_from_summary(summary: str | None) -> list[str]:
    text = str(summary or "")
    without_today = remove_empty_today_focus_sections(remove_generic_today_focus_items(text))
    items: list[str] = []

    def add_item(value: str) -> None:
        clean = normalize_today_focus_line_text(value)
        if not clean or is_generic_today_focus_line(clean) or is_meta_uncertainty_line(clean):
            return
        if clean not in items:
            items.append(clean)

    for homework_item in build_today_focus_items_from_homework(without_today):
        add_item(homework_item)

    return items[:3]


def normalize_today_focus_markers(text: str | None) -> str:
    normalized = normalize_learning_flow_markers(text)
    for marker in TODAY_FOCUS_MARKERS:
        normalized = normalized.replace(marker, STANDARD_TODAY_FOCUS_MARKER)
    return normalized


def remove_all_today_focus_sections(text: str | None) -> str:
    lines = str(text or "").splitlines()
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip() not in TODAY_FOCUS_MARKERS:
            result.append(line.rstrip())
            index += 1
            continue

        index += 1
        while index < len(lines) and not today_focus_section_boundary(lines[index]):
            index += 1

    cleaned = "\n".join(result)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def ensure_required_today_focus_section(summary: str | None) -> str:
    text = normalize_today_focus_markers(summary).strip()
    if not text:
        return ""
    existing_items = summary_today_focus_items(text)
    generated_items = [] if existing_items else build_today_focus_items_from_summary(text)
    today_items = existing_items or generated_items

    text_without_today = remove_all_today_focus_sections(text)
    today_block = f"{STANDARD_TODAY_FOCUS_MARKER}\n\n수업 전 확인"
    if today_items:
        today_block += "\n" + "\n".join(f"- {item}" for item in today_items)
    return "\n\n".join(part for part in [today_block, text_without_today] if part.strip()).strip()


def remove_generic_today_focus_items(text: str) -> str:
    if not text:
        return ""

    lines = str(text).splitlines()
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped not in TODAY_FOCUS_MARKERS:
            result.append(line.rstrip())
            index += 1
            continue

        marker = stripped
        section_lines: list[str] = []
        index += 1
        while index < len(lines) and not today_focus_section_boundary(lines[index]):
            section_lines.append(lines[index])
            index += 1

        cleaned_section = clean_today_focus_section_lines(section_lines)
        if cleaned_section:
            result.append(marker)
            result.append("")
            result.extend(cleaned_section)
        elif any(line.strip() for line in section_lines):
            result.append(marker)

    cleaned_text = "\n".join(result)
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)
    return cleaned_text.strip()


def remove_parent_message_sections(summary: str) -> str:
    headings = [
        r"\d+\.\s*학부모\s*전달\s*문구",
        r"\d+\.\s*학부모한테\s*드릴\s*문자",
        r"\[학부모\s*전달\s*문구\]",
        r"\[학부모한테\s*드릴\s*문자\]",
    ]
    lines = summary.splitlines()
    kept_lines = []
    skipping = False
    for line in lines:
        is_parent_heading = any(re.match(pattern, line.strip()) for pattern in headings)
        is_next_numbered_heading = bool(re.match(r"\d+\.\s+", line.strip()))
        is_bracket_heading = bool(re.match(r"\[[^\]]+\]", line.strip()))

        if is_parent_heading:
            skipping = True
            continue
        if skipping and (is_next_numbered_heading or is_bracket_heading):
            skipping = False
        if not skipping:
            kept_lines.append(line)

    cleaned = "\n".join(kept_lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def remove_status_detail_labels(summary: str) -> str:
    cleaned = re.sub(r"(?m)^\s*[-*]\s*판정\s*:\s*(.*)$", r"- [\1]", summary)
    cleaned = re.sub(r"(?m)^\s*[-*]\s*내용\s*:\s*(.*)$", r"- \1", cleaned)
    cleaned = re.sub(r"\[확인\s*안\s*됨\]\s*", "", cleaned)
    return cleaned.replace("- []", "-")


def remove_misc_record_section(summary: str) -> str:
    cleaned = re.sub(
        r"(?ms)^\s*\d+\.\s*기타\s*기록\s*\n(?:\s*[-*].*?(?:\n|$))*",
        "",
        summary,
    )
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def remove_empty_placeholder_lines(summary: str) -> str:
    placeholder_patterns = [
        r"^\s*[-*]\s*$",
        r"^\s*[-*]\s*\[(?:해당 없음|확인 안 됨)\]\s*$",
        r"^\s*[-*]\s*\[해당 없음\]\s*(?:해당\s*없음|없음|내용\s*없음|관련\s*내용\s*없음)\.?\s*$",
        r"^\s*[-*]\s*\[확인 안 됨\]\s*(?:숙제|구두\s*요청사항|요청사항)\s*(?:이행\s*여부|진행\s*여부)?(?:가|는)?\s*(?:이번\s*상담에서\s*)?확인(?:되지\s*않음|되지\s*않았습니다|되지\s*않음\.)\.?\s*$",
        r"^\s*[-*]\s*(?:없음|해당\s*없음|내용\s*없음|관련\s*내용\s*없음|특이사항(?:은|이)?\s*확인(?:되지\s*않음|되지\s*않았습니다))\.?\s*$",
    ]
    cleaned_lines = []
    for line in summary.splitlines():
        if any(re.match(pattern, line.strip()) for pattern in placeholder_patterns):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def detect_suspicious_transcript(transcript_text: str) -> str:
    text = transcript_text or ""
    stripped = text.strip()
    if not stripped:
        return "원문이 비어 있습니다."
    if len(stripped) < 30:
        return "원문 글자 수가 30자 미만입니다."

    prompt_phrase = "교재명, 단어 암기, 테스트, 수업 태도, 복습, 오답"
    if stripped.count(prompt_phrase) >= 2:
        return "기존 음성 인식 힌트 문구가 반복되었습니다."

    sentences = split_transcript_sentences(stripped)
    sentence_counts: dict[str, int] = {}
    for sentence in sentences:
        normalized_sentence = re.sub(r"\s+", " ", sentence).strip()
        # Short back-and-forth 상담 발화는 자연스럽게 반복된다. 긴 문장만 환각 반복으로 본다.
        if len(normalized_sentence) < 20:
            continue
        sentence_counts[normalized_sentence] = sentence_counts.get(normalized_sentence, 0) + 1
    if sentence_counts:
        max_sentence_count = max(sentence_counts.values())
        total_counted_sentences = sum(sentence_counts.values())
        if max_sentence_count >= 8 and max_sentence_count / max(total_counted_sentences, 1) >= 0.45:
            return "같은 긴 문장이 원문 대부분에서 반복되었습니다."

    compact = re.sub(r"\s+", "", stripped)
    for size in (80, 60, 40):
        chunks: dict[str, int] = {}
        for index in range(0, max(len(compact) - size + 1, 0), size):
            chunk = compact[index:index + size]
            if len(chunk) == size:
                chunks[chunk] = chunks.get(chunk, 0) + 1
        if chunks:
            max_chunk_count = max(chunks.values())
            total_chunks = sum(chunks.values())
        else:
            max_chunk_count = 0
            total_chunks = 0
        if max_chunk_count >= 6 and max_chunk_count / max(total_chunks, 1) >= 0.35:
            return "동일한 긴 문구가 반복적으로 등장했습니다."

    meaningful_sentences = [
        re.sub(r"\s+", " ", sentence).strip()
        for sentence in sentences
        if len(re.sub(r"\s+", " ", sentence).strip()) >= 20
    ]
    unique_sentences = set(meaningful_sentences)
    if len(stripped) >= 500 and len(meaningful_sentences) >= 8 and len(unique_sentences) <= 2:
        return "원문 길이에 비해 고유 문장 수가 너무 적습니다."

    return ""


def build_admin_today_focus_block(today_focus_text: str) -> str:
    text = (today_focus_text or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    if lines and re.match(r"^\s*\[(?:오늘\s*상담할\s*것|오늘\s*바로\s*확인할\s*것|오늘\s*확인할\s*것)\]\s*$", lines[0]):
        lines = lines[1:]
    body = "\n".join(line.rstrip() for line in lines).strip()
    if not body:
        return ""
    return f"[오늘 확인할 것]\n{body}".strip()


def merge_today_focus_block(existing_notes: str | None, today_focus_text: str) -> str:
    base_notes = existing_notes if existing_notes is not None else ""
    new_block = build_admin_today_focus_block(today_focus_text)
    if not new_block:
        return base_notes
    return new_block
