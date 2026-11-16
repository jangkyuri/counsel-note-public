from __future__ import annotations

import hashlib
import re
from datetime import date


LOG_EXPORT_REQUIRED_COLUMNS = {"date", "name", "notes"}


LOG_EXPORT_CONTENT_COLUMNS = (
    "notes",
    "tests",
    "progress",
    "homework_row",
    "homework_book",
    "homework_current",
    "homework_progress",
    "homework_comment",
    "homework_next",
)

LOG_EXPORT_HOMEWORK_COLUMNS = (
    "homework_row",
    "homework_book",
    "homework_current",
    "homework_progress",
    "homework_comment",
    "homework_next",
)


def _truthy_result_value(value: object) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _false_result_value(value: object) -> bool:
    if value is False:
        return True
    if value is True or value is None:
        return False
    return str(value).strip().lower() in {"0", "false", "no", "n"}


def _result_status_text(result: dict[str, object]) -> str:
    return str(
        result.get("result_status")
        or result.get("status")
        or result.get("결과")
        or ""
    ).strip()


def _result_message_text(result: dict[str, object]) -> str:
    return str(result.get("result_message") or result.get("message") or result.get("메시지") or "").strip()


def _result_review_category(result: dict[str, object]) -> str:
    return str(result.get("review_category") or "").strip()


def _result_is_lock_timeout(result: dict[str, object]) -> bool:
    status = _result_status_text(result).lower()
    review_category = _result_review_category(result).lower()
    error_type = str(result.get("error_type") or "").strip().lower()
    message = _result_message_text(result).lower()
    return (
        status == "lock_timeout_retryable"
        or review_category in {"lock_timeout", "lock_timeout_retryable"}
        or error_type in {"lock_timeout", "lock_timeout_retryable"}
        or ("lockservice" in message or "잠금" in message)
    )


def _result_is_timeout_unknown(result: dict[str, object]) -> bool:
    status = _result_status_text(result).lower()
    return _truthy_result_value(result.get("timeout_unknown")) or status == "timeout_unknown"


def _result_is_unsafe_similar(result: dict[str, object]) -> bool:
    reason = str(result.get("duplicate_reason") or "").strip().lower()
    message = _result_message_text(result).lower()
    review_category = _result_review_category(result).lower()
    return (
        _truthy_result_value(result.get("unsafe_similar_record"))
        or _truthy_result_value(result.get("duplicate_write_suspected"))
        or reason == "unsafe_similar_record"
        or review_category == "unsafe_similar_record"
        or message == "similar_record_needs_review"
    )


def _result_is_updated_existing(result: dict[str, object]) -> bool:
    status = _result_status_text(result)
    message = _result_message_text(result)
    review_category = _result_review_category(result)
    return (
        _truthy_result_value(result.get("updated_existing"))
        or status in {"updated_existing_progress_only", "updated_existing_format_only", "updated_existing_same_date"}
        or message in {"updated_existing_progress_only", "updated_existing_format_only", "updated_existing_same_date"}
        or review_category
        in {"updated_existing_progress_only", "updated_existing_format_only", "updated_existing_same_date"}
    )


def _result_is_saved_confirmed_after_timeout(result: dict[str, object]) -> bool:
    status = _result_status_text(result)
    review_category = _result_review_category(result)
    return status == "saved_confirmed_after_timeout" or review_category == "saved_confirmed_after_timeout"


def _result_is_already_saved(result: dict[str, object]) -> bool:
    status = _result_status_text(result)
    message = _result_message_text(result).lower()
    return (
        _truthy_result_value(result.get("already_saved_no_write"))
        or _truthy_result_value(result.get("duplicate_skipped"))
        or status in {"중복으로 추가하지 않음", "이미 저장됨", "already_saved", "duplicate_skipped"}
        or message.startswith("duplicate_skipped")
    )


def _result_is_failure(result: dict[str, object]) -> bool:
    if _result_is_timeout_unknown(result) or _result_is_lock_timeout(result):
        return False
    status = _result_status_text(result)
    status_lower = status.lower()
    error_type = str(result.get("error_type") or "").strip().lower()
    if "실패" in status:
        return True
    if status_lower in {"failed", "failure", "failed_retryable", "error", "apps_script_error"}:
        return True
    if error_type in {"permission_or_doc_not_found", "permission", "doc_not_found", "document_not_found"}:
        return True
    return _false_result_value(result.get("ok", ""))


def _result_needs_document_review(result: dict[str, object]) -> bool:
    if _result_is_timeout_unknown(result) or _result_is_lock_timeout(result):
        return False
    if _truthy_result_value(result.get("needs_review")):
        return True
    if _truthy_result_value(result.get("dedupe_key_stale")):
        return True
    review_category = _result_review_category(result)
    if review_category in {"added_row_review", "stale_dedupe_key_review"}:
        return True
    review_reason = str(result.get("review_reason") or "").lower()
    return any(
        keyword in review_reason
        for keyword in (
            "fallback",
            "서식 fallback",
            "서식 복사 실패",
            "복사 실패",
            "행/칸 위치 확인",
            "위치 확인",
            "서식 확인 필요",
        )
    )


def _result_is_new_saved(result: dict[str, object]) -> bool:
    status = _result_status_text(result)
    status_lower = status.lower()
    return (
        status in {"성공", "신규 저장"}
        or status_lower in {"success", "added", "newly_added", "saved"}
        or _truthy_result_value(result.get("newly_added"))
        or (
            _truthy_result_value(result.get("ok"))
            and not _result_is_already_saved(result)
            and not _result_is_updated_existing(result)
        )
    )


def _classification(
    *,
    group: str,
    label: str,
    compact_section: str | None,
    show_in_compact_list: bool,
    count_key: str,
    retry_allowed: bool,
    retry_scope: str,
    is_success_like: bool,
    is_failure_like: bool,
    docs_link_allowed: bool = True,
) -> dict[str, object]:
    return {
        "group": group,
        "label": label,
        "compact_section": compact_section,
        "show_in_compact_list": show_in_compact_list,
        "count_key": count_key,
        "retry_allowed": retry_allowed,
        "retry_scope": retry_scope,
        "is_success_like": is_success_like,
        "is_failure_like": is_failure_like,
        "docs_link_allowed": docs_link_allowed,
    }


def classify_google_docs_save_result(result: dict[str, object] | None) -> dict[str, object]:
    """Classify one Google Docs save result for counts, compact UI, and retry policy."""
    item = result or {}

    if _result_is_failure(item):
        return _classification(
            group="failed",
            label="실패",
            compact_section="실패",
            show_in_compact_list=True,
            count_key="failed",
            retry_allowed=True,
            retry_scope="same_student_only",
            is_success_like=False,
            is_failure_like=True,
        )
    if _result_is_unsafe_similar(item):
        return _classification(
            group="needs_check",
            label="중복 저장 의심",
            compact_section="확인 필요",
            show_in_compact_list=True,
            count_key="duplicate_write_suspected",
            retry_allowed=False,
            retry_scope="none",
            is_success_like=False,
            is_failure_like=False,
        )
    if _result_is_lock_timeout(item):
        return _classification(
            group="needs_check",
            label="잠금 시간초과",
            compact_section="확인 필요",
            show_in_compact_list=True,
            count_key="lock_timeout_retryable",
            retry_allowed=True,
            retry_scope="same_student_only",
            is_success_like=False,
            is_failure_like=False,
        )
    if _result_is_timeout_unknown(item):
        return _classification(
            group="needs_check",
            label="추가 여부 불확실",
            compact_section="확인 필요",
            show_in_compact_list=True,
            count_key="timeout_unknown",
            retry_allowed=False,
            retry_scope="same_student_only",
            is_success_like=False,
            is_failure_like=False,
        )
    if _result_needs_document_review(item):
        return _classification(
            group="needs_check",
            label="문서 확인 필요",
            compact_section="확인 필요",
            show_in_compact_list=True,
            count_key="needs_review",
            retry_allowed=False,
            retry_scope="none",
            is_success_like=False,
            is_failure_like=False,
        )
    if _result_is_saved_confirmed_after_timeout(item):
        return _classification(
            group="already_processed",
            label="저장 확인됨 · 응답 지연",
            compact_section="이미 처리됨",
            show_in_compact_list=True,
            count_key="saved_confirmed_after_timeout",
            retry_allowed=False,
            retry_scope="none",
            is_success_like=True,
            is_failure_like=False,
        )
    if _result_is_updated_existing(item):
        return _classification(
            group="already_processed",
            label="기존 기록 업데이트",
            compact_section="이미 처리됨",
            show_in_compact_list=True,
            count_key="updated_existing",
            retry_allowed=False,
            retry_scope="none",
            is_success_like=True,
            is_failure_like=False,
        )
    if _result_is_already_saved(item):
        return _classification(
            group="already_processed",
            label="이미 저장됨",
            compact_section="이미 처리됨",
            show_in_compact_list=True,
            count_key="already_saved",
            retry_allowed=False,
            retry_scope="none",
            is_success_like=True,
            is_failure_like=False,
        )

    return _classification(
        group="new_saved",
        label="신규 저장",
        compact_section=None,
        show_in_compact_list=False,
        count_key="new_saved",
        retry_allowed=False,
        retry_scope="none",
        is_success_like=True,
        is_failure_like=False,
    )


def log_csv_confirmed_student_keys(results: list[dict[str, object]]) -> set[tuple[str, str]]:
    students: set[tuple[str, str]] = set()
    for result in results:
        classification = classify_google_docs_save_result(result)
        if not classification["is_success_like"]:
            continue
        if classification["count_key"] == "new_saved" and not _result_is_new_saved(result):
            continue
        try:
            source_date = date.fromisoformat(str(result.get("date") or "").strip()).isoformat()
        except ValueError:
            continue
        doc_id = str(result.get("doc_id") or "").strip()
        if not doc_id:
            doc_match = re.search(r"/document/d/([^/?#]+)", str(result.get("doc_url") or ""))
            doc_id = doc_match.group(1) if doc_match else ""
        name = re.sub(r"\s+", "", str(result.get("name") or "")).casefold()
        identity = f"doc:{doc_id}" if doc_id else f"name:{name}" if name else ""
        if identity:
            students.add((source_date, hashlib.sha256(identity.encode("utf-8")).hexdigest()))
    return students


def validate_log_export_columns(columns: list[str]) -> list[str]:
    existing_columns = {column.strip() for column in columns}
    return sorted(LOG_EXPORT_REQUIRED_COLUMNS - existing_columns)


def short_preview_text(value: str, max_length: int = 48) -> str:
    clean_value = (value or "").strip()
    if len(clean_value) <= max_length:
        return clean_value or "-"
    return clean_value[: max_length - 1] + "…"


def normalize_log_notes_text(value: str | None) -> str:
    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).strip()


def normalize_log_dedupe_value(value: str | None) -> str:
    normalized = normalize_log_notes_text(value)
    lines = [re.sub(r"\s+", " ", line.strip()) for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line)


def clean_dedupe_key_part(value: str | None) -> str:
    cleaned = normalize_log_dedupe_value(value).replace("|", "/")
    return cleaned or "-"


def build_log_csv_dedupe_key(row: dict[str, str]) -> str:
    dedupe_fields = [
        "date",
        "sid",
        "name",
        "curriculum",
        "notes",
        "progress",
        "homework_book",
        "homework_progress",
        "homework_next",
    ]
    canonical = "\n".join(
        f"{field}:{normalize_log_dedupe_value(row.get(field))}"
        for field in dedupe_fields
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return "|".join(
        [
            "log_csv",
            clean_dedupe_key_part(row.get("date")),
            clean_dedupe_key_part(row.get("sid")),
            clean_dedupe_key_part(row.get("name")),
            digest,
        ]
    )


def log_row_has_docs_content(row: dict[str, str]) -> bool:
    # ADMIN class-record exports contain actual attendees, including rows without details.
    if str(row.get("date") or "").strip() and str(row.get("name") or "").strip():
        return True
    return any(normalize_log_notes_text(row.get(column)) for column in LOG_EXPORT_CONTENT_COLUMNS)


def is_homework_check_only_row(row: dict[str, str]) -> bool:
    if not normalize_log_notes_text(row.get("homework_row")):
        return False
    return not any(
        normalize_log_notes_text(row.get(column))
        for column in LOG_EXPORT_HOMEWORK_COLUMNS
        if column != "homework_row"
    )


def build_docs_preview_from_log_row(row: dict[str, str]) -> str:
    date_text = (row.get("date") or "").strip()
    name = (row.get("name") or "").strip()
    curriculum = (row.get("curriculum") or "").strip()
    notes = normalize_log_notes_text(row.get("notes"))
    tests = (row.get("tests") or "").strip()
    progress = (row.get("progress") or "").strip()
    homework_row = (row.get("homework_row") or "").strip()
    homework_book = (row.get("homework_book") or "").strip()
    homework_current = (row.get("homework_current") or "").strip()
    homework_progress = (row.get("homework_progress") or "").strip()
    homework_next = (row.get("homework_next") or "").strip()
    homework_comment = (row.get("homework_comment") or "").strip()

    title = f"[{date_text}] {name}".strip()
    if curriculum:
        title += f" / {curriculum}"

    parts = [title]
    if date_text and name:
        parts.extend(["", "출결: 출석"])
    if notes:
        parts.extend(["", notes])
    if tests:
        parts.extend(["", "", f"테스트: {tests}"])
    if progress:
        parts.extend(["", "", f"진도: {progress}"])

    homework_lines = []
    if homework_row:
        homework_lines.append(
            "- 검사: 완료" if is_homework_check_only_row(row) else f"- 기록: {homework_row}"
        )
    if homework_book:
        homework_lines.append(f"- 교재: {homework_book}")
    if homework_current:
        homework_lines.append(f"- 현재 숙제: {homework_current}")
    if homework_progress:
        homework_lines.append(f"- 진행률: {homework_progress}")
    if homework_next:
        homework_lines.append(f"- 다음 숙제: {homework_next}")
    if homework_comment:
        homework_lines.append(f"- 메모: {homework_comment}")
    if homework_lines:
        parts.extend(["", "", "숙제:", *homework_lines])

    return "\n".join(part for part in parts if part is not None).strip()


def _normalize_homework_merge_value(value: str | None) -> str:
    normalized = normalize_log_notes_text(value)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[\s\.,，。．;；:：!！?？\"'“”‘’()\[\]{}<>]+", "", normalized)
    return normalized.lower()


def _split_homework_books(value: str | None) -> list[str]:
    raw = re.sub(r"\s+", " ", str(value or "").strip())
    if not raw:
        return []
    books: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"\s*(?:,|，|、|/|\+|&|및|그리고)\s*", raw):
        book = part.strip()
        key = _normalize_homework_merge_value(book)
        if book and key not in seen:
            seen.add(key)
            books.append(book)
    return books


def _strip_homework_section_from_preview(preview: str) -> str:
    kept_lines: list[str] = []
    in_homework = False
    for line in normalize_log_notes_text(preview).splitlines():
        if line.strip() == "숙제:":
            in_homework = True
            continue
        if in_homework:
            continue
        kept_lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()


def _homework_details_from_row(row: dict[str, str]) -> list[tuple[str, str]]:
    details: list[tuple[str, str]] = []
    for label, column in (
        ("현재 숙제", "homework_current"),
        ("진행률", "homework_progress"),
        ("다음 숙제", "homework_next"),
        ("메모", "homework_comment"),
    ):
        value = normalize_log_notes_text(row.get(column))
        if value:
            details.append((label, value))
    if not details and is_homework_check_only_row(row):
        details.append(("검사", "완료"))
    return details


def _homework_details_key(details: list[tuple[str, str]]) -> str:
    return "|".join(f"{label}:{_normalize_homework_merge_value(value)}" for label, value in details)


def _format_homework_detail_text(details: list[tuple[str, str]], with_colon: bool = False) -> str:
    if with_colon:
        return ", ".join(f"{label}: {value}" for label, value in details)
    return ", ".join(f"{label} {value}" for label, value in details)


def _merged_homework_lines(rows: list[dict[str, str]]) -> list[str]:
    books: list[str] = []
    book_keys: set[str] = set()
    assignments: list[tuple[str, list[tuple[str, str]]]] = []
    assignment_seen: set[tuple[str, str]] = set()
    generic_details: list[list[tuple[str, str]]] = []
    generic_seen: set[str] = set()

    for row in rows:
        row_books = _split_homework_books(row.get("homework_book"))
        for book in row_books:
            key = _normalize_homework_merge_value(book)
            if key and key not in book_keys:
                book_keys.add(key)
                books.append(book)
        details = _homework_details_from_row(row)
        if not details:
            continue
        details_key = _homework_details_key(details)
        if row_books:
            for book in row_books:
                key = (_normalize_homework_merge_value(book), details_key)
                if key not in assignment_seen:
                    assignment_seen.add(key)
                    assignments.append((book, details))
        elif details_key not in generic_seen:
            generic_seen.add(details_key)
            generic_details.append(details)

    lines: list[str] = []
    detail_keys = {_homework_details_key(details) for _book, details in assignments if details}
    if assignments and len(detail_keys) == 1:
        if books:
            lines.append(f"- 교재: {', '.join(books)}")
        common_details = assignments[0][1]
        for label, value in common_details:
            lines.append(f"- {label}: {value}")
    elif assignments:
        for book, details in assignments:
            if details:
                lines.append(f"- {book}: {_format_homework_detail_text(details)}")
    elif books:
        lines.append(f"- 교재: {', '.join(books)}")

    for details in generic_details:
        details_key = _homework_details_key(details)
        if details_key not in detail_keys:
            for label, value in details:
                lines.append(f"- {label}: {value}")

    return lines


def _merge_log_rows_for_homework_duplicates(rows: list[dict[str, str]]) -> tuple[dict[str, str], str]:
    base_row = dict(rows[0])
    core_preview = _strip_homework_section_from_preview(build_docs_preview_from_log_row(base_row))
    homework_lines = _merged_homework_lines(rows)
    merged_preview = core_preview
    if homework_lines:
        merged_preview = "\n\n".join(part for part in [core_preview, "숙제:\n" + "\n".join(homework_lines)] if part).strip()

    books: list[str] = []
    book_keys: set[str] = set()
    for row in rows:
        for book in _split_homework_books(row.get("homework_book")):
            key = _normalize_homework_merge_value(book)
            if key and key not in book_keys:
                book_keys.add(key)
                books.append(book)
    if books:
        base_row["homework_book"] = ", ".join(books)

    for column in ("homework_current", "homework_progress", "homework_next", "homework_comment"):
        values: list[str] = []
        seen: set[str] = set()
        for row in rows:
            value = normalize_log_notes_text(row.get(column))
            key = _normalize_homework_merge_value(value)
            if value and key not in seen:
                seen.add(key)
                values.append(value)
        base_row[column] = values[0] if len(values) == 1 else " / ".join(values)

    return base_row, merged_preview


def merge_homework_duplicate_log_rows(matched_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    buckets: dict[tuple[str, str, str, str], list[tuple[int, dict[str, object]]]] = {}
    for index, item in enumerate(matched_rows):
        row = item["source_row"]
        docs_matches = item.get("docs_matches") or []
        if len(docs_matches) != 1:
            continue
        doc_id = str(docs_matches[0].get("doc_id") or docs_matches[0].get("doc_url") or "").strip()
        date_text = str(row.get("date") or "").strip()
        name = str(row.get("name") or "").strip()
        core_preview = _strip_homework_section_from_preview(str(item.get("preview") or ""))
        core_key = _normalize_homework_merge_value(core_preview)
        if not (doc_id and date_text and name and core_key):
            continue
        buckets.setdefault((date_text, name, doc_id, core_key), []).append((index, item))

    merge_indexes: set[int] = set()
    merged_by_first_index: dict[int, dict[str, object]] = {}
    for candidates in buckets.values():
        if len(candidates) < 2:
            continue
        source_rows = [candidate["source_row"] for _index, candidate in candidates]
        if not any(any(normalize_log_notes_text(row.get(column)) for column in LOG_EXPORT_HOMEWORK_COLUMNS) for row in source_rows):
            continue
        first_index, first_item = candidates[0]
        merged_row, merged_preview = _merge_log_rows_for_homework_duplicates(source_rows)
        merged_item = dict(first_item)
        merged_item["source_row"] = merged_row
        merged_item["preview"] = merged_preview
        merged_item["merged_duplicate_homework"] = True
        merged_item["merged_homework_items"] = [
            str(candidate.get("row_index", "")) for _index, candidate in candidates
        ]
        merged_item["merged_homework_count"] = len(candidates)
        merged_item["status"] = "숙제 병합됨" if first_item.get("selected") else first_item.get("status", "")
        merged_by_first_index[first_index] = merged_item
        merge_indexes.update(index for index, _item in candidates[1:])

    if not merge_indexes:
        return matched_rows

    merged_rows: list[dict[str, object]] = []
    for index, item in enumerate(matched_rows):
        if index in merge_indexes:
            continue
        merged_rows.append(merged_by_first_index.get(index, item))
    return merged_rows


def match_log_rows_to_docs(
    log_rows: list[dict[str, str]],
    docs_map_rows: list[dict[str, str]],
) -> list[dict[str, object]]:
    docs_by_name: dict[str, list[dict[str, str]]] = {}
    for docs_row in docs_map_rows:
        student_name = (docs_row.get("student_name") or "").strip()
        if not student_name:
            continue
        if not docs_row.get("doc_id") and not docs_row.get("doc_url"):
            continue
        docs_by_name.setdefault(student_name, []).append(docs_row)

    matched_rows: list[dict[str, object]] = []
    for index, row in enumerate(log_rows, start=1):
        name = (row.get("name") or "").strip()
        notes = normalize_log_notes_text(row.get("notes"))
        docs_matches = docs_by_name.get(name, [])
        notes_length = len(notes)
        has_docs_content = log_row_has_docs_content(row)

        if not has_docs_content:
            selected = False
            docs_status = "매칭" if len(docs_matches) == 1 else "미매칭"
            status = "기록 내용 없음"
        elif len(docs_matches) == 0:
            selected = False
            docs_status = "미매칭"
            status = "Docs 매핑 없음"
        elif len(docs_matches) > 1:
            selected = False
            docs_status = "중복"
            status = "Docs 매핑 중복"
        else:
            selected = True
            docs_status = "매칭"
            status = "미리보기 가능"

        if len(docs_matches) == 1:
            doc_target = docs_matches[0].get("doc_id") or docs_matches[0].get("doc_url") or "-"
        elif len(docs_matches) > 1:
            doc_target = f"{len(docs_matches)}개 후보"
        else:
            doc_target = "-"

        matched_rows.append(
            {
                "row_index": index,
                "source_row": row,
                "docs_matches": docs_matches,
                "selected": selected,
                "docs_status": docs_status,
                "doc_target": doc_target,
                "status": status,
                "notes_length": notes_length,
                "preview": build_docs_preview_from_log_row(row),
            }
        )
    return matched_rows

