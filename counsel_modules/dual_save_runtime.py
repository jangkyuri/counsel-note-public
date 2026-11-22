from __future__ import annotations

import os
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

from counsel_modules.dual_save import (
    Lesson, Outcome, SaveReview, SaveUnavailable, Snapshot, canonical_subject, identity,
    save_missing, select_lesson,
)
from counsel_modules.dual_save_docs import Json, JsonMap, document_snapshot, document_update, mapping, sequence

_ADMIN = "http://127.0.0.1:8003"
_DOCS = "https://docs.googleapis.com/v1/documents/"
_SCOPE = "https://www.googleapis.com/auth/documents"


class JsonClient(Protocol):
    def get(self, url: str, *, params: dict[str, str] | None = None) -> Json: ...
    def post(self, url: str, *, json: JsonMap) -> Json: ...


class TitleMismatch(SaveReview):
    """The matched document does not identify this student."""


def enabled() -> bool:
    return os.environ.get("CONNECT_LOG_DUAL_SAVE_ENABLED", "") == "1"


def _field(row: JsonMap, key: str) -> str:
    value = row.get(key)
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def load_lessons(client: JsonClient, day: date) -> list[Lesson]:
    filters = {"date": day.isoformat()}
    roster = sequence(client.get(_ADMIN + "/api/attend", params=filters))
    flags = mapping(client.get(_ADMIN + "/api/attendance", params=filters))
    lessons: list[Lesson] = []
    for value in roster:
        row = mapping(value)
        sid, name = _field(row, "id"), _field(row, "name")
        if flags.get(sid) != 1 or not sid or not name:
            continue
        subjects = {canonical_subject(_field(row, key)) for key in ("curriculum", "subCurriculum")}
        subjects.discard("")
        lessons.append(Lesson(sid, name, day, next(iter(subjects)) if len(subjects) == 1 else ""))
    return lessons


def select_doc_id(name: str, candidates: list[tuple[str, str]]) -> str:
    docs = {doc_id for mapped_name, doc_id in candidates if identity(mapped_name) == identity(name)}
    if len(docs) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{15,}", doc_id := next(iter(docs))):
        raise SaveReview()
    if any(doc == doc_id and identity(other) != identity(name) for other, doc in candidates):
        raise SaveReview()
    return doc_id


@dataclass(frozen=True, slots=True)
class AdminNotes:
    client: JsonClient
    lesson: Lesson

    def read(self) -> Snapshot:
        payload = mapping(self.client.get(_ADMIN + "/api/logs/entry", params={
            "date": self.lesson.day.isoformat(), "sid": self.lesson.sid,
        }))
        if payload.get("ok") is not True:
            raise SaveUnavailable()
        entry = mapping(payload.get("entry"))
        text = entry.get("notes", "")
        if text is None:
            text = ""
        if not isinstance(text, str):
            raise SaveReview()
        return Snapshot(text, "")

    def append(self, base: Snapshot, addition: str) -> None:
        payload = mapping(self.client.post(_ADMIN + "/api/logs/patch", json={
            "date": self.lesson.day.isoformat(), "sid": self.lesson.sid,
            "entry": {"notes": base.text + addition, "updatedAt": datetime.now(UTC).isoformat()},
            "base": {"notes": base.text},
        }))
        if payload.get("ok") is not True:
            raise SaveUnavailable()


@dataclass(frozen=True, slots=True)
class DocsNotes:
    client: JsonClient
    lesson: Lesson
    doc_id: str

    def read(self) -> Snapshot:
        payload = mapping(self.client.get(_DOCS + self.doc_id, params={
            "includeTabsContent": "true", "suggestionsViewMode": "SUGGESTIONS_INLINE",
        }))
        title = _field(payload, "title")
        title_words = re.findall(r"[^\W_]+", identity(title))
        name_words = re.findall(r"[^\W_]+", identity(self.lesson.name))
        if not name_words or not any(
            title_words[i:i + len(name_words)] == name_words for i in range(len(title_words))
        ):
            raise TitleMismatch()
        return document_snapshot(payload, self.lesson.day, self.lesson.subject)

    def append(self, base: Snapshot, addition: str) -> None:
        self.client.post(_DOCS + self.doc_id + ":batchUpdate", json=document_update(
            base, self.lesson.day, self.lesson.subject, addition,
        ))


@dataclass(frozen=True, slots=True)
class SaveResult:
    admin: Outcome
    docs: Outcome
    sid: str = ""
    doc_id: str = ""
    subject: str = ""


def process_lesson(
    admin_client: JsonClient, docs_client: JsonClient, name: str, day: date, summary: str,
    docs_map: list[tuple[str, str]], previous: SaveResult,
    on_target: Callable[[Lesson, str], None] | None = None,
) -> SaveResult:
    try:
        lesson = select_lesson(name, day, load_lessons(admin_client, day))
        if lesson is None:
            raise SaveReview()
        doc = select_doc_id(lesson.name, docs_map)
        if previous.sid and (previous.sid, previous.doc_id, previous.subject) != (lesson.sid, doc, lesson.subject):
            raise SaveReview()
    except SaveReview:
        return SaveResult(Outcome.REVIEW, Outcome.REVIEW, previous.sid, previous.doc_id, previous.subject)
    except SaveUnavailable:
        return previous
    docs = DocsNotes(docs_client, lesson, doc)
    if not previous.docs.verified:
        try:
            docs.read()
        except TitleMismatch:
            return SaveResult(Outcome.REVIEW, Outcome.REVIEW, lesson.sid, doc, lesson.subject)
        except (SaveReview, SaveUnavailable):
            # ADMIN has an independently confirmed lesson; Docs does its own read before writing.
            pass
    if on_target:
        on_target(lesson, doc)
    admin_status = previous.admin if previous.admin.verified else save_missing(AdminNotes(admin_client, lesson), summary)
    docs_status = previous.docs if previous.docs.verified else save_missing(docs, summary)
    return SaveResult(admin_status, docs_status, lesson.sid, doc, lesson.subject)


@dataclass(frozen=True, slots=True)
class _Transport:
    request: Callable[..., Json]

    def get(self, url: str, *, params: dict[str, str] | None = None) -> Json:
        return self.request("GET", url, params=params)

    def post(self, url: str, *, json: JsonMap) -> Json:
        return self.request("POST", url, json=json)


def save_on_server(
    name: str, day: date, summary: str, docs_map: list[tuple[str, str]], previous: SaveResult,
    on_target: Callable[[Lesson, str], None],
) -> SaveResult:
    """Use a private ADMIN origin and the server's explicitly approved Google credential."""
    credential_file = os.environ.get("CONNECT_LOG_DUAL_SAVE_GOOGLE_CREDENTIALS", "").strip()
    if not enabled() or not credential_file:
        return previous
    try:
        import httpx2
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        return previous
    try:
        credentials = Credentials.from_authorized_user_file(credential_file, scopes=[_SCOPE])
    except (OSError, ValueError, GoogleAuthError):
        return previous
    limits = httpx2.Limits(max_connections=200, max_keepalive_connections=40, keepalive_expiry=30.0)
    timeout = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
    sockets = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]
    transport = httpx2.HTTPTransport(http2=True, limits=limits, retries=0, socket_options=sockets)
    with httpx2.Client(transport=transport, timeout=timeout, trust_env=False, follow_redirects=False) as client:
        def request(method: str, url: str, **kwargs: Json) -> Json:
            headers: dict[str, str] = {}
            try:
                if url.startswith(_DOCS):
                    credentials.before_request(Request(), method, url, headers)
                elif not url.startswith(_ADMIN + "/api/"):
                    raise SaveUnavailable()
                response = client.request(method, url, headers=headers, **kwargs)
                if response.status_code == 409:
                    raise SaveReview()
                response.raise_for_status()
                return response.json()
            except (httpx2.HTTPError, GoogleAuthError, ValueError):
                raise SaveUnavailable() from None

        bridge = _Transport(request)
        return process_lesson(bridge, bridge, name, day, summary, docs_map, previous, on_target)
