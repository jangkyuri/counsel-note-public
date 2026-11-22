from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from counsel_modules.dual_save import Outcome, identity
from counsel_modules.dual_save_runtime import SaveResult


class ReplayConflict(RuntimeError):
    """A prepared recording cannot be redirected to a different target."""


def recording_replay_key(path: Path, student_name: str, counseling_day: str) -> str:
    with path.open("rb") as audio:
        digest = hashlib.file_digest(audio, "sha256").hexdigest()
    return digest + "." + _target_key(student_name, counseling_day)


def _target_key(student_name: str, counseling_day: str) -> str:
    scope = json.dumps(["clg-audio-v1", identity(student_name), counseling_day])
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()


def replay_key_matches_target(key: str, student_name: str, counseling_day: str) -> bool:
    return key.partition(".")[2] == _target_key(student_name, counseling_day)


@dataclass(frozen=True, slots=True)
class PreparedRecording:
    summary: str
    export: str
    result: SaveResult


@dataclass(frozen=True, slots=True)
class RecordingReplay:
    path: Path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute("""
                CREATE TABLE IF NOT EXISTS prepared_recordings (
                    replay_key TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    export TEXT NOT NULL,
                    sid TEXT NOT NULL DEFAULT '',
                    doc_id TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL DEFAULT '',
                    admin_confirmed INTEGER NOT NULL DEFAULT 0,
                    docs_confirmed INTEGER NOT NULL DEFAULT 0
                )
            """)
            yield connection

    @staticmethod
    def _read(connection: sqlite3.Connection, key: str) -> PreparedRecording | None:
        row = connection.execute(
            "SELECT * FROM prepared_recordings WHERE replay_key = ?", (key,),
        ).fetchone()
        if row is None:
            return None
        return PreparedRecording(
            row["summary"], row["export"],
            SaveResult(
                Outcome.EXISTING if row["admin_confirmed"] else Outcome.PENDING,
                Outcome.EXISTING if row["docs_confirmed"] else Outcome.PENDING,
                row["sid"], row["doc_id"], row["subject"],
            ),
        )

    def find(self, key: str) -> PreparedRecording | None:
        if not self.path.is_file():
            return None
        with self._connection() as connection:
            return self._read(connection, key)

    def claim(self, key: str, summary: str, export: str) -> PreparedRecording:
        """First prepared output wins, even if another browser tab finishes at the same time."""
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO prepared_recordings (replay_key, summary, export) VALUES (?, ?, ?)",
                (key, summary, export),
            )
            item = self._read(connection, key)
            if item is None:
                raise ReplayConflict()
            return item

    def bind(self, key: str, sid: str, doc_id: str, subject: str) -> None:
        with self._connection() as connection:
            touched = connection.execute("""
                UPDATE prepared_recordings SET sid = ?, doc_id = ?, subject = ?
                WHERE replay_key = ? AND (sid = '' OR (sid = ? AND doc_id = ? AND subject = ?))
            """, (sid, doc_id, subject, key, sid, doc_id, subject)).rowcount
            if touched != 1:
                raise ReplayConflict()

    def confirm(self, key: str, result: SaveResult) -> None:
        if not (result.admin.verified or result.docs.verified):
            return
        with self._connection() as connection:
            touched = connection.execute("""
                UPDATE prepared_recordings
                SET admin_confirmed = MAX(admin_confirmed, ?), docs_confirmed = MAX(docs_confirmed, ?)
                WHERE replay_key = ? AND sid = ? AND doc_id = ? AND subject = ?
            """, (
                result.admin.verified, result.docs.verified, key, result.sid, result.doc_id, result.subject,
            )).rowcount
            if touched != 1:
                raise ReplayConflict()
