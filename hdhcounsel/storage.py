"""Atomic local records, with raw evidence separate from editable review notes."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .domain import Transcript


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".hdh-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True)
class Record:
    path: Path
    student: str
    counseling_date: str
    transcript: Transcript

    @classmethod
    def create(cls, root: Path, student: str, counseling_date: str, transcript: Transcript) -> Record:
        # Names are content, never filesystem path components.
        path = root / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}"
        path.mkdir(parents=True)
        record = cls(path, student, counseling_date, transcript)
        write_json(path / "transcript.json", {
            "schema_version": 1,
            "student": student,
            "counseling_date": counseling_date,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "transcript": transcript.to_dict(),
        })
        return record

    def save_review(self, notes: str, speakers: dict[str, str], reviewed: bool) -> None:
        write_json(self.path / "review.json", {
            "schema_version": 1,
            "notes": notes,
            "speakers": speakers,
            "reviewed": reviewed,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
