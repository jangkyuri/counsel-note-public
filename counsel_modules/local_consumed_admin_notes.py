from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parents[1]
CONSUMED_ADMIN_NOTES_PATH = BASE_DIR / "data" / "local_consumed_admin_notes.json"
REGISTRY_VERSION = 2
PREVIEW_MAX_LENGTH = 80

SUCCESS_SAVE_STATUSES = {
    "added",
    "success",
    "saved",
    "newly_added",
    "duplicate_skipped",
    "already_saved",
    "updated_existing",
    "updated_existing_progress_only",
    "updated_existing_format_only",
    "saved_confirmed_after_timeout",
    "성공",
    "기존 기록 업데이트",
    "중복으로 추가하지 않음",
    "이미 저장됨",
}

BLOCKED_SAVE_STATUSES = {
    "timeout_unknown",
    "lock_timeout_retryable",
    "failed",
    "failure",
    "needs_review",
    "duplicate_write_suspected",
    "unsafe_similar_record",
    "추가 여부 불확실",
    "잠금 시간초과",
    "실패",
    "문서 확인 필요",
    "중복 저장 의심",
}

TOO_SHORT_NOTES = {"", "-", "확인", "미정", "없음", "해당없음", "해당 없음", "없음."}


def _empty_registry(**extra: Any) -> dict[str, Any]:
    registry: dict[str, Any] = {"version": REGISTRY_VERSION, "items": []}
    registry.update(extra)
    return registry


def _registry_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else CONSUMED_ADMIN_NOTES_PATH


def normalize_admin_note_for_hash(note_text: object) -> str:
    text = str(note_text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.splitlines():
        clean_line = re.sub(r"^\s*[-*ㆍ·•]+\s*", "", line.strip())
        if clean_line:
            lines.append(clean_line)
    text = " ".join(lines)
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = re.sub(r"[\"'“”‘’\[\]{}<>]+", "", text)
    text = re.sub(r"\s*[:：;,，。.!?！？]+\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _note_is_consumable(note_text: object) -> bool:
    normalized = normalize_admin_note_for_hash(note_text)
    return len(normalized) >= 5 and normalized not in TOO_SHORT_NOTES


def build_student_key(student_name: object | None = None, admin_sid: object | None = None) -> str:
    clean_sid = re.sub(r"\s+", "", str(admin_sid or "").strip())
    if clean_sid:
        return f"sid:{clean_sid.lower()}"
    clean_name = re.sub(r"\s+", "", str(student_name or "").strip())
    return f"name:{clean_name.lower()}" if clean_name else ""


def _normalize_counseling_date(value: object | None) -> str:
    text = str(value or "").strip()
    date_match = re.match(r"^(\d{4})\D+(\d{1,2})\D+(\d{1,2})(?:\D|$)", text)
    if not date_match:
        return text
    year, month, day = (int(part) for part in date_match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def _registry_item_matches_date(item: dict[str, Any], counseling_date: str) -> bool:
    item_date = _normalize_counseling_date(
        item.get("counseling_date") or item.get("date")
    )
    return item_date == counseling_date


def build_admin_note_hash(student_key: str, note_text: object) -> str:
    normalized = normalize_admin_note_for_hash(note_text)
    canonical = f"{student_key}\n{normalized}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _note_preview(note_text: object, max_length: int = PREVIEW_MAX_LENGTH) -> str:
    preview = re.sub(r"\s+", " ", str(note_text or "").strip())
    if len(preview) <= max_length:
        return preview
    return preview[: max_length - 1] + "…"


def load_consumed_admin_notes(registry_path: str | Path | None = None) -> dict[str, Any]:
    path = _registry_path(registry_path)
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            registry = _empty_registry()
            path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
            return registry
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("registry root must be an object")
        items = data.get("items")
        if not isinstance(items, list):
            data["items"] = []
        data.setdefault("version", REGISTRY_VERSION)
        return data
    except Exception as exc:
        try:
            if path.exists():
                timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
                backup_path = path.with_name(f"{path.stem}_corrupt_{timestamp}{path.suffix}")
                shutil.copy2(path, backup_path)
        except Exception:
            pass
        return _empty_registry(load_error=str(exc))


def save_consumed_admin_notes(
    registry: dict[str, Any],
    registry_path: str | Path | None = None,
) -> dict[str, Any]:
    path = _registry_path(registry_path)
    temp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": int(registry.get("version") or REGISTRY_VERSION),
            "items": list(registry.get("items") or []),
        }
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            json.dump(data, temp_file, ensure_ascii=False, indent=2)
            temp_path = Path(temp_file.name)

        for attempt in range(5):
            try:
                temp_path.replace(path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * (2**attempt))
        return {"ok": True, "path": str(path)}
    except Exception as exc:
        return {"ok": False, "path": str(path), "error": str(exc)}
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _registry_timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d-%H%M%S")


def backup_consumed_admin_notes(registry_path: str | Path | None = None) -> dict[str, Any]:
    path = _registry_path(registry_path)
    try:
        if not path.exists():
            return {"ok": True, "path": str(path), "backup_path": "", "skipped_reason": "missing_registry"}
        backup_path = path.with_name(f"{path.stem}.backup.{_registry_timestamp()}{path.suffix}")
        shutil.copy2(path, backup_path)
        return {"ok": True, "path": str(path), "backup_path": str(backup_path)}
    except Exception as exc:
        return {"ok": False, "path": str(path), "backup_path": "", "error": str(exc)}


def diagnose_consumed_admin_notes(registry_path: str | Path | None = None) -> dict[str, Any]:
    path = _registry_path(registry_path)
    diagnostic: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "load_ok": True,
        "load_error": "",
        "version": None,
        "item_count": 0,
        "duplicate_count": 0,
        "missing_required_count": 0,
        "recent_items": [],
        "backup_files": [],
    }
    try:
        backup_patterns = [
            f"{path.stem}.backup.*{path.suffix}",
            f"{path.stem}_corrupt_*{path.suffix}",
        ]
        backups: list[Path] = []
        if path.parent.exists():
            for pattern in backup_patterns:
                backups.extend(path.parent.glob(pattern))
        diagnostic["backup_files"] = [
            str(item)
            for item in sorted(set(backups), key=lambda item: item.stat().st_mtime, reverse=True)[:5]
            if item.exists()
        ]
    except Exception:
        diagnostic["backup_files"] = []

    if not path.exists():
        return diagnostic

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("registry root must be an object")
        items = data.get("items")
        if not isinstance(items, list):
            raise ValueError("registry items must be a list")
        seen: set[tuple[str, str, str]] = set()
        duplicate_count = 0
        missing_required_count = 0
        for item in items:
            if not isinstance(item, dict):
                missing_required_count += 1
                continue
            student_key = str(item.get("student_key") or "")
            note_hash = str(item.get("note_hash") or "")
            if not student_key or not note_hash:
                missing_required_count += 1
            pair = (
                student_key,
                _normalize_counseling_date(item.get("counseling_date") or item.get("date")),
                note_hash,
            )
            if student_key and note_hash:
                if pair in seen:
                    duplicate_count += 1
                seen.add(pair)
        recent_items = []
        for item in items[-10:]:
            if not isinstance(item, dict):
                continue
            recent_items.append(
                {
                    "consumed_at": item.get("consumed_at", ""),
                    "source": item.get("source", ""),
                    "save_status": item.get("save_status", ""),
                    "counseling_date": item.get("counseling_date", ""),
                    "note_preview": _note_preview(item.get("note_preview", ""), 80),
                }
            )
        diagnostic.update(
            {
                "version": data.get("version"),
                "item_count": len(items),
                "duplicate_count": duplicate_count,
                "missing_required_count": missing_required_count,
                "recent_items": recent_items,
            }
        )
    except Exception as exc:
        diagnostic["load_ok"] = False
        diagnostic["load_error"] = str(exc)
    return diagnostic


def _coerce_notes(notes: object) -> tuple[list[str], str]:
    if notes is None:
        return [], "list"
    if isinstance(notes, str):
        return [line for line in notes.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()], "text"
    if isinstance(notes, (list, tuple)):
        return [str(note) for note in notes if str(note or "").strip()], "list"
    return [str(notes)], "list"


def _restore_notes_type(notes: list[str], notes_type: str) -> list[str] | str:
    if notes_type == "text":
        return "\n".join(notes).strip()
    return notes


def _docs_section_key(line: object) -> str:
    stripped = str(line or "").strip().strip("*").strip()
    bracket_text = stripped[1:-1].strip() if stripped.startswith("[") and stripped.endswith("]") else stripped
    compact = re.sub(r"[\s:：]+", "", bracket_text)
    if compact in {"오늘상담할것", "오늘바로확인할것", "오늘확인할것", "수업전확인", "다음수업전확인"}:
        return "today"
    if compact in {"상담참고사항", "참고사항", "특이사항", "요청사항", "구두요청사항"}:
        return "notes"
    if compact in {"테스트배정", "테스트", "평가배정"}:
        return "test"
    if compact in {"숙제"}:
        return "homework"
    if re.match(r"^(?:진도|차시|수업\s*진도|오늘\s*진도|진행\s*차시)(?:\s*\([^)]*\))?\s*[:：]", stripped):
        return "progress"
    if re.match(r"^\[[^\]]+\]\s*$", stripped) or re.match(r"^\*[^*].+?\*\s*$", stripped):
        return "other"
    return ""


def _is_today_subheading(line: object) -> bool:
    compact = re.sub(r"[\s:：]+", "", str(line or "").strip().strip("*").strip())
    return compact in {"수업전확인", "다음수업전확인", "오늘확인할것", "확인할항목", "숙제체크"}


def _strip_section_bullet(line: object) -> str:
    return re.sub(r"^\s*[-*ㆍ·•]+\s*", "", str(line or "").strip()).strip()


def _consumable_admin_note_candidates(notes: object) -> tuple[list[tuple[str, bool]], str]:
    original_notes, notes_type = _coerce_notes(notes)
    has_section_headings = any(_docs_section_key(note) for note in original_notes)
    current_section = ""
    candidates: list[tuple[str, bool]] = []
    for note in original_notes:
        section_key = _docs_section_key(note)
        if section_key:
            current_section = section_key
            candidates.append((note, False))
            continue
        if has_section_headings:
            is_consumable = (
                current_section in {"today", "notes", "test"}
                and not _is_today_subheading(note)
                and _note_is_consumable(note)
            )
        else:
            is_consumable = _note_is_consumable(note)
        candidates.append((note, is_consumable))
    return candidates, notes_type


def _source_lookup_key(line: object) -> str:
    return normalize_admin_note_for_hash(_strip_section_bullet(line))


def extract_consumable_admin_section_notes_from_docs_text(text: object) -> list[str]:
    notes: list[str] = []
    current_section = ""
    for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        section_key = _docs_section_key(line)
        if section_key:
            current_section = section_key
            continue
        if current_section not in {"today", "notes", "test"}:
            continue
        if not str(line or "").strip() or _is_today_subheading(line):
            continue
        candidate = _strip_section_bullet(line)
        if _note_is_consumable(candidate):
            notes.append(candidate)
    return notes


def _admin_source_note_keys(admin_source_notes: object) -> set[str]:
    source_notes, _notes_type = _coerce_notes(admin_source_notes)
    keys: set[str] = set()
    for note in source_notes:
        normalized = normalize_admin_note_for_hash(note)
        if normalized:
            keys.add(normalized)
    return keys


def _line_is_admin_derived(
    candidate: str,
    *,
    admin_source_keys: set[str],
    source_by_line: dict[str, str] | None,
) -> bool:
    candidate_key = normalize_admin_note_for_hash(candidate)
    if not candidate_key:
        return False
    if source_by_line is not None:
        source = str(source_by_line.get(candidate_key) or source_by_line.get(candidate) or "").strip()
        if source:
            return source == "admin_note"
        return False
    if not admin_source_keys:
        return False
    return any(candidate_key == key or candidate_key in key or key in candidate_key for key in admin_source_keys)


def _line_is_consumed_admin_note(
    student_key: str,
    candidate: str,
    consumed_hashes: set[str],
) -> bool:
    if not _note_is_consumable(candidate):
        return False
    return build_admin_note_hash(student_key, candidate) in consumed_hashes


def filter_consumed_admin_sections_from_docs_text(
    student_name: object | None,
    admin_sid: object | None,
    docs_text: object,
    *,
    enabled: bool = True,
    admin_source_notes: object | None = None,
    source_by_line: dict[str, str] | None = None,
    registry: dict[str, Any] | None = None,
    registry_path: str | Path | None = None,
    counseling_date: object | None = None,
) -> dict[str, Any]:
    original_text = str(docs_text or "")
    try:
        if not enabled:
            return {
                "filtered_text": original_text.strip(),
                "removed_notes": [],
                "diagnostic": {"enabled": False, "removed_count": 0, "failed_open": False},
            }
        student_key = build_student_key(student_name, admin_sid)
        if not student_key:
            return {
                "filtered_text": original_text.strip(),
                "removed_notes": [],
                "diagnostic": {"enabled": enabled, "removed_count": 0, "missing_student_key": True},
            }
        loaded_registry = registry if registry is not None else load_consumed_admin_notes(registry_path)
        normalized_counseling_date = _normalize_counseling_date(counseling_date)
        consumed_hashes = {
            str(item.get("note_hash") or "")
            for item in loaded_registry.get("items", [])
            if str(item.get("student_key") or "") == student_key
            and _registry_item_matches_date(item, normalized_counseling_date)
        }
        admin_source_keys = _admin_source_note_keys(admin_source_notes)
        lines = original_text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        result: list[str] = []
        removed: list[str] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            section_key = _docs_section_key(line)
            if section_key not in {"today", "notes", "test"}:
                result.append(line.rstrip())
                index += 1
                continue

            section_lines = [line.rstrip()]
            index += 1
            while index < len(lines):
                next_key = _docs_section_key(lines[index])
                if section_key == "today" and next_key == "today" and _is_today_subheading(lines[index]):
                    section_lines.append(lines[index].rstrip())
                    index += 1
                    continue
                if next_key and next_key != "progress":
                    break
                if next_key == "progress":
                    break
                section_lines.append(lines[index].rstrip())
                index += 1

            kept_section: list[str] = []
            kept_content_count = 0
            for section_line in section_lines:
                stripped = section_line.strip()
                if not stripped:
                    kept_section.append(section_line.rstrip())
                    continue
                line_key = _docs_section_key(section_line)
                if line_key in {"today", "notes", "test"} or _is_today_subheading(section_line):
                    kept_section.append(section_line.rstrip())
                    continue
                candidate = _strip_section_bullet(section_line)
                is_admin_derived = _line_is_admin_derived(
                    candidate,
                    admin_source_keys=admin_source_keys,
                    source_by_line=source_by_line,
                )
                candidate_hash = build_admin_note_hash(student_key, candidate)
                should_remove = is_admin_derived and _line_is_consumed_admin_note(
                    student_key,
                    candidate,
                    consumed_hashes,
                )
                if should_remove:
                    removed.append(candidate)
                    continue
                kept_section.append(section_line.rstrip())
                kept_content_count += 1
                if is_admin_derived and _note_is_consumable(candidate):
                    consumed_hashes.add(candidate_hash)

            if kept_content_count:
                while kept_section and not kept_section[0].strip():
                    kept_section.pop(0)
                while kept_section and not kept_section[-1].strip():
                    kept_section.pop()
                result.extend(kept_section)

        filtered_text = "\n".join(result)
        filtered_text = re.sub(r"\n{3,}", "\n\n", filtered_text).strip()
        return {
            "filtered_text": filtered_text,
            "removed_notes": removed,
            "diagnostic": {
                "enabled": enabled,
                "removed_count": len(removed),
                "student_key": student_key,
                "counseling_date": normalized_counseling_date,
                "registry_load_error": loaded_registry.get("load_error", ""),
                "failed_open": False,
            },
        }
    except Exception as exc:
        return {
            "filtered_text": original_text.strip(),
            "removed_notes": [],
            "diagnostic": {"enabled": enabled, "removed_count": 0, "failed_open": True, "error": str(exc)},
        }


def filter_consumed_admin_notes(
    student_name: object | None,
    admin_sid: object | None,
    notes: object,
    enabled: bool = True,
    registry: dict[str, Any] | None = None,
    registry_path: str | Path | None = None,
    counseling_date: object | None = None,
) -> dict[str, Any]:
    note_candidates, notes_type = _consumable_admin_note_candidates(notes)
    original_notes = [note for note, _is_consumable in note_candidates]
    try:
        if not enabled:
            return {
                "filtered_notes": _restore_notes_type(original_notes, notes_type),
                "removed_notes": [],
                "diagnostic": {"enabled": False, "removed_count": 0, "failed_open": False},
            }
        student_key = build_student_key(student_name, admin_sid)
        if not student_key:
            return {
                "filtered_notes": _restore_notes_type(original_notes, notes_type),
                "removed_notes": [],
                "diagnostic": {"enabled": enabled, "removed_count": 0, "missing_student_key": True},
            }
        loaded_registry = registry if registry is not None else load_consumed_admin_notes(registry_path)
        normalized_counseling_date = _normalize_counseling_date(counseling_date)
        consumed_hashes = {
            str(item.get("note_hash") or "")
            for item in loaded_registry.get("items", [])
            if str(item.get("student_key") or "") == student_key
            and _registry_item_matches_date(item, normalized_counseling_date)
        }
        filtered: list[str] = []
        removed: list[str] = []
        for note, is_consumable in note_candidates:
            note_hash = build_admin_note_hash(student_key, note)
            if is_consumable and note_hash in consumed_hashes:
                removed.append(note)
            else:
                filtered.append(note)
                if is_consumable:
                    consumed_hashes.add(note_hash)
        return {
            "filtered_notes": _restore_notes_type(filtered, notes_type),
            "removed_notes": removed,
            "diagnostic": {
                "enabled": enabled,
                "removed_count": len(removed),
                "student_key": student_key,
                "counseling_date": normalized_counseling_date,
                "registry_load_error": loaded_registry.get("load_error", ""),
                "failed_open": False,
            },
        }
    except Exception as exc:
        return {
            "filtered_notes": _restore_notes_type(original_notes, notes_type),
            "removed_notes": [],
            "diagnostic": {"enabled": enabled, "removed_count": 0, "failed_open": True, "error": str(exc)},
        }


def _save_status_is_consumable(save_status: object) -> bool:
    status = str(save_status or "").strip()
    status_lower = status.lower()
    if status in BLOCKED_SAVE_STATUSES or status_lower in BLOCKED_SAVE_STATUSES:
        return False
    if status in SUCCESS_SAVE_STATUSES or status_lower in SUCCESS_SAVE_STATUSES:
        return True
    return False


def mark_admin_notes_consumed(
    student_name: object | None,
    admin_sid: object | None,
    notes: object,
    save_status: object,
    final_text: object | None = None,
    registry_path: str | Path | None = None,
    counseling_date: object | None = None,
) -> dict[str, Any]:
    try:
        if not _save_status_is_consumable(save_status):
            return {"ok": True, "marked_count": 0, "skipped_reason": "status_not_consumable"}
        student_key = build_student_key(student_name, admin_sid)
        if not student_key:
            return {"ok": True, "marked_count": 0, "skipped_reason": "missing_student_key"}
        note_candidates, _notes_type = _consumable_admin_note_candidates(notes)
        if not note_candidates:
            return {"ok": True, "marked_count": 0, "skipped_reason": "no_notes"}
        normalized_final_text = normalize_admin_note_for_hash(final_text)
        if not normalized_final_text:
            return {"ok": True, "marked_count": 0, "skipped_reason": "empty_final_text"}
        registry = load_consumed_admin_notes(registry_path)
        items = list(registry.get("items") or [])
        normalized_counseling_date = _normalize_counseling_date(counseling_date)
        existing_keys = {
            (
                str(item.get("note_hash") or ""),
                _normalize_counseling_date(item.get("counseling_date") or item.get("date")),
            )
            for item in items
        }
        now_text = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
        marked_count = 0
        for note, is_consumable in note_candidates:
            if not is_consumable:
                continue
            normalized_note = normalize_admin_note_for_hash(note)
            if normalized_note not in normalized_final_text:
                continue
            note_hash = build_admin_note_hash(student_key, note)
            note_key = (note_hash, normalized_counseling_date)
            if note_key in existing_keys:
                continue
            items.append(
                {
                    "student_key": student_key,
                    "note_hash": note_hash,
                    "counseling_date": normalized_counseling_date,
                    "note_preview": _note_preview(note),
                    "consumed_at": now_text,
                    "source": "admin_note",
                    "consumed_by": "google_docs_save",
                    "save_status": str(save_status or ""),
                }
            )
            existing_keys.add(note_key)
            marked_count += 1
        if marked_count:
            registry["version"] = REGISTRY_VERSION
            registry["items"] = items
            save_result = save_consumed_admin_notes(registry, registry_path)
            if not save_result.get("ok"):
                return {"ok": False, "marked_count": 0, "error": save_result.get("error", "")}
        return {"ok": True, "marked_count": marked_count}
    except Exception as exc:
        return {"ok": False, "marked_count": 0, "error": str(exc)}


def reset_consumed_admin_notes(
    registry_path: str | Path | None = None,
    *,
    backup: bool = True,
) -> dict[str, Any]:
    backup_result = {"ok": True, "backup_path": "", "skipped_reason": "backup_disabled"}
    if backup:
        backup_result = backup_consumed_admin_notes(registry_path)
        if not backup_result.get("ok"):
            return {
                "ok": False,
                "path": str(_registry_path(registry_path)),
                "backup_path": "",
                "error": f"backup failed: {backup_result.get('error', '')}",
            }
    save_result = save_consumed_admin_notes(_empty_registry(), registry_path)
    save_result["backup_path"] = backup_result.get("backup_path", "")
    save_result["backup_skipped_reason"] = backup_result.get("skipped_reason", "")
    return save_result
