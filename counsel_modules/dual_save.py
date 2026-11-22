from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol


class Outcome(StrEnum):
    SAVED = "saved"
    EXISTING = "existing"
    PENDING = "pending"
    REVIEW = "review"

    @property
    def verified(self) -> bool:
        return self in (Outcome.SAVED, Outcome.EXISTING)


class SaveUnavailable(RuntimeError):
    """A request did not produce a trustworthy result."""


class SaveReview(RuntimeError):
    """The destination or existing record is ambiguous."""


@dataclass(frozen=True, slots=True)
class Lesson:
    sid: str
    name: str
    day: date
    subject: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    text: str
    revision: str
    index: int = 0
    date_index: int = 0
    tab_id: str = ""


class Notes(Protocol):
    def read(self) -> Snapshot: ...
    def append(self, base: Snapshot, addition: str) -> None: ...


def identity(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def canonical_subject(value: str) -> str:
    key = "".join(unicodedata.normalize("NFKC", value).casefold().split())
    key = re.sub(r"(?:apex|a:ble|able|\uae30\ubcf8)$", "", key)
    for short, full in (
        ("\uacf5\uc2181", "\uacf5\ud1b5\uc218\ud5591"), ("\uacf5\uc2182", "\uacf5\ud1b5\uc218\ud5592"),
        ("\ubbf8\uc8011", "\ubbf8\uc801\ubd841"), ("\ubbf8\uc8012", "\ubbf8\uc801\ubd842"),
        ("\ud655\ud1b5", "\ud655\ub960\uacfc\ud1b5\uacc4"), ("\uae30\ud558", "\uae30\ud558"), ("\ub300\uc218", "\ub300\uc218"),
    ):
        if key in (short, full):
            return short
    return ""


def select_lesson(name: str, day: date, lessons: Iterable[Lesson]) -> Lesson | None:
    name_key = identity(name)
    if not name_key:
        return None
    candidates = {
        lesson for lesson in lessons
        if lesson.day == day and identity(lesson.name) == name_key
    }
    if len(candidates) != 1:
        return None
    lesson = next(iter(candidates))
    return lesson if lesson.sid and lesson.subject else None


_HEADINGS = re.compile(
    r"^\[[^\[\]]+\]$|^\**\s*\uc0c1\ub2f4\s*\ucc38\uace0\uc0ac\ud56d\s*\**\s*[:\uff1a]?$"
    r"|^\**\s*(?:\uc219\uc81c|\ud14c\uc2a4\ud2b8|\uc9c4\ub3c4)\s*\**\s*[:\uff1a]$"
)


def _line_key(line: str) -> str:
    return identity(re.sub(r"^\s*[-*\u2022]\s+", "", line))


def missing_text(existing: str, summary: str) -> str:
    """Compare complete lines, not substrings or guessed semantic similarities."""
    seen = {_line_key(line) for line in existing.splitlines() if line.strip()}
    missing: list[str] = []
    heading = ""
    for raw in summary.splitlines():
        line = raw.strip()
        key = _line_key(line)
        if not key:
            continue
        if _HEADINGS.fullmatch(line):
            heading = line
            continue
        if key in seen:
            continue
        if heading:
            missing.append(heading)
            heading = ""
        missing.append(line)
        seen.add(key)
    return "\n".join(missing)


def save_missing(notes: Notes, summary: str) -> Outcome:
    if not missing_text("", summary):
        return Outcome.REVIEW
    try:
        before = notes.read()
    except SaveReview:
        return Outcome.REVIEW
    except SaveUnavailable:
        return Outcome.PENDING
    missing = missing_text(before.text, summary)
    if not missing:
        return Outcome.EXISTING
    addition = ("\n\n" if before.text else "") + missing
    try:
        notes.append(before, addition)
    except SaveReview:
        return Outcome.REVIEW
    except SaveUnavailable:
        # A lost response is not permission to write again.
        pass
    try:
        after = notes.read()
    except SaveReview:
        return Outcome.REVIEW
    except SaveUnavailable:
        return Outcome.PENDING
    if after.text.startswith(before.text) and not missing_text(after.text, summary):
        return Outcome.SAVED
    return Outcome.PENDING
