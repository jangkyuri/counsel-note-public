"""The stable, serializable transcript contract; no ML or UI dependencies."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import CounselError


@dataclass(frozen=True)
class Word:
    text: str
    start: float | None = None
    end: float | None = None
    speaker: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    words: tuple[Word, ...] = ()

    def __post_init__(self) -> None:
        if not all(math.isfinite(t) for t in (self.start, self.end)):
            raise CounselError("The speech engine returned a non-finite timestamp.")
        if self.start < 0 or self.end < self.start:
            raise CounselError("The speech engine returned an invalid timestamp range.")


@dataclass(frozen=True)
class Transcript:
    segments: tuple[Segment, ...]
    duration: float
    language: str = "ko"
    engine: str = "whisperx-3.8.6/large-v3"
    diarized: bool = False
    model_revisions: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_whisperx(cls, result: dict[str, Any], duration: float, diarized: bool) -> Transcript:
        segments = tuple(
            Segment(
                start=float(row["start"]),
                end=float(row["end"]),
                text=str(row["text"]).strip(),
                speaker=row.get("speaker"),
                words=tuple(
                    Word(
                        text=str(word["word"]),
                        start=word.get("start"),
                        end=word.get("end"),
                        speaker=word.get("speaker"),
                        score=word.get("score"),
                    )
                    for word in row.get("words", [])
                ),
            )
            for row in result["segments"]
            if str(row["text"]).strip()
        )
        return cls(segments, duration, diarized=diarized)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        data = dict(data)
        data["segments"] = tuple(
            Segment(**{**row, "words": tuple(Word(**word) for word in row["words"])})
            for row in data["segments"]
        )
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def text(self, speakers: dict[str, str] | None = None) -> str:
        labels = speakers or {}
        return "\n".join(
            f"[{timestamp(row.start)} - {timestamp(row.end)}] "
            f"{labels.get(row.speaker, row.speaker) or 'UNKNOWN'}: {row.text}"
            for row in self.segments
        )


def timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"
