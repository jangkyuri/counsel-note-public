from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import TypeAlias

from counsel_modules.dual_save import SaveReview, Snapshot, canonical_subject

Json: TypeAlias = 'str | int | float | bool | None | list[Json] | dict[str, Json]'
JsonMap: TypeAlias = dict[str, Json]

_DATE = re.compile(r"^(?:(20\d{2})\s*[-./\ub144]\s*)?(\d{1,2})\s*[-./\uc6d4]\s*(\d{1,2})(?!\d)")
_SUBJECT = re.compile(r"^\s*\uacfc\ubaa9\s*[:\uff1a]\s*(.+?)\s*$", re.MULTILINE)
_SUBJECT_LABEL = "\uacfc\ubaa9: "


def mapping(value: Json) -> JsonMap:
    if not isinstance(value, dict):
        raise SaveReview()
    return value


def sequence(value: Json) -> list[Json]:
    if not isinstance(value, list):
        raise SaveReview()
    return value


@dataclass(frozen=True, slots=True)
class Cell:
    text: str
    index: int


def _cell(value: Json) -> Cell:
    structures = [mapping(item) for item in sequence(mapping(value).get("content"))]
    paragraphs = [item for item in structures if "paragraph" in item]
    if not paragraphs:
        return Cell("", 0)
    end = paragraphs[-1].get("endIndex")
    editable = isinstance(end, int) and len(paragraphs) == len(structures)
    fragments: list[str] = []
    for item in paragraphs:
        for element in sequence(mapping(item["paragraph"]).get("elements")):
            piece = mapping(element)
            if piece.get("suggestedInsertionIds") or piece.get("suggestedDeletionIds"):
                editable = False
            run = piece.get("textRun")
            if not isinstance(run, dict) or not isinstance(text := run.get("content"), str):
                editable = False
                continue
            fragments.append(text)
    text = "".join(fragments)
    return Cell(text[:-1] if text.endswith("\n") else text, int(end) - 1 if editable else 0)


def _tables(tabs: list[Json]) -> Iterator[tuple[str, list[list[Cell]]]]:
    for value in tabs:
        tab = mapping(value)
        tab_id = mapping(tab.get("tabProperties")).get("tabId")
        if not isinstance(tab_id, str) or not tab_id:
            raise SaveReview()
        document = tab.get("documentTab")
        if document:
            body = mapping(mapping(document).get("body"))
            for block in sequence(body.get("content")):
                table = mapping(block).get("table")
                if not table:
                    continue
                rows = sequence(mapping(table).get("tableRows"))
                yield tab_id, [
                    [_cell(cell) for cell in sequence(mapping(row).get("tableCells"))]
                    for row in rows
                ]
        children = tab.get("childTabs")
        if children:
            yield from _tables(sequence(children))


def _header(row: list[Cell]) -> int | None:
    for idx, cell in enumerate(row):
        key = "".join(cell.text.split())
        if all(term in key for term in ("\uc0c1\ub2f4\uc77c\uc790", "\uc0c1\ub2f4\uc790", "\uc138\ubd80\ub0b4\uc6a9")):
            return idx if idx else None
        if "\uc0c1\ub2f4\uc138\ubd80\ub0b4\uc6a9" in key:
            detail = min(idx + 1, len(row) - 1)
            return detail if detail else None
    if row and "".join(row[0].text.split()) in {"\uc0c1\ub2f4\uc77c\uc790", "\uc0c1\ub2f4\uc77c"}:
        cols = [i for i, cell in enumerate(row[1:], 1) if "".join(cell.text.split()) == "\uc138\ubd80\ub0b4\uc6a9"]
        return cols[0] if len(cols) == 1 else None
    return None


def document_snapshot(doc: JsonMap, day: date, subject: str) -> Snapshot:
    revision = doc.get("revisionId")
    if not isinstance(revision, str) or not revision:
        raise SaveReview()
    matches = [
        (tab_id, rows[header_index + 1:], detail)
        for tab_id, rows in _tables(sequence(doc.get("tabs")))
        for header_index, row in enumerate(rows)
        if (detail := _header(row)) is not None
    ]
    if len(matches) != 1:
        raise SaveReview()
    tab_id, rows, detail_column = matches[0]
    exact: list[Cell] = []
    slots: list[tuple[Cell, Cell]] = []
    for row in rows:
        if len(row) <= detail_column:
            slots.clear()
            continue
        date_cell, note = row[0], row[detail_column]
        if all(cell.index and not cell.text.strip() for cell in row):
            slots.append((date_cell, note))
            continue
        slots.clear()
        label = _DATE.match(date_cell.text.strip())
        if not label or (int(label[2]), int(label[3])) != (day.month, day.day):
            continue
        if not label[1]:
            raise SaveReview()
        if int(label[1]) != day.year:
            continue
        subjects = _SUBJECT.findall(date_cell.text)
        if len(subjects) != 1 or not (record_subject := canonical_subject(subjects[0])):
            raise SaveReview()
        if record_subject == subject:
            exact.append(note)
    if len(exact) > 1 or (exact and not exact[0].index):
        raise SaveReview()
    if exact:
        return Snapshot(exact[0].text, revision, exact[0].index, tab_id=tab_id)
    if not slots:
        raise SaveReview()
    date_cell, note = slots[0]
    return Snapshot("", revision, note.index, date_cell.index, tab_id)


def document_update(base: Snapshot, day: date, subject: str, addition: str) -> JsonMap:
    changes: list[Json] = [
        {"insertText": {"location": {"index": base.index, "tabId": base.tab_id}, "text": addition}},
    ]
    if base.date_index:
        changes.append({"insertText": {
            "location": {"index": base.date_index, "tabId": base.tab_id},
            "text": day.isoformat() + "\n" + _SUBJECT_LABEL + subject,
        }})
    return {"writeControl": {"requiredRevisionId": base.revision}, "requests": changes}
