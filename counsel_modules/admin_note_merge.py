from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AdminSummaryMergeConflict(ValueError):
    reason: str

    def __str__(self) -> str:
        return "기존 정리본이 중복되거나 변경되어 저장 내용을 확인해야 합니다."


def normalized_admin_notes(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def merge_admin_summary(existing: str, exported: str, previous_export: str = "") -> str:
    """Preserve unrelated notes and replace only the previously confirmed block."""
    normalized = normalized_admin_notes(existing)
    exported_normalized = normalized_admin_notes(exported)
    occurrences = normalized.count(exported_normalized) if exported_normalized else 0
    if occurrences > 1:
        raise AdminSummaryMergeConflict("duplicate_current")
    if previous_export.strip():
        pattern = re.compile(r"\s+".join(re.escape(part) for part in previous_export.strip().split()))
        matches = list(pattern.finditer(existing))
        if len(matches) > 1:
            raise AdminSummaryMergeConflict("duplicate_previous")
        if matches:
            match = matches[0]
            return existing[:match.start()] + exported + existing[match.end():]
        if normalized_admin_notes(previous_export) in normalized:
            raise AdminSummaryMergeConflict("changed_previous")
    if occurrences == 1:
        return existing
    return existing + ("\n\n" if existing else "") + exported
