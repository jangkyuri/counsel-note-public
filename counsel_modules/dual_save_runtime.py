from __future__ import annotations

import os
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from counsel_modules.dual_save import (
    Lesson, Notes, Outcome, SaveReview, SaveUnavailable, Snapshot, TitleMismatch,
    canonical_subject, identity, save_missing, select_lesson,
)
from counsel_modules.dual_save_docs import Json, JsonMap, document_snapshot, document_update, mapping, sequence
from counsel_modules.dual_save_script import SCRIPT_EXEC_URL, SECRET, ScriptDocsNotes

_ADMIN = "http://127.0.0.1:8003"
_DOCS = "https://docs.googleapis.com/v1/documents/"
_SCOPE = "https://www.googleapis.com/auth/documents"


class JsonClient(Protocol):
    def get(self, url: str, *, params: dict[str, str] | None = None) -> Json: ...
    def post(self, url: str, *, json: JsonMap) -> Json: ...


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
    docs_factory: Callable[[JsonClient, Lesson, str], Notes] = DocsNotes,
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
    docs = docs_factory(docs_client, lesson, doc)
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
    if not enabled():
        return previous
    script_url = os.environ.get("CONNECT_LOG_DUAL_SAVE_APPS_SCRIPT_URL", "").strip()
    secret_file = os.environ.get("CONNECT_LOG_DUAL_SAVE_APPS_SCRIPT_SECRET_FILE", "").strip()
    docs_factory: Callable[[JsonClient, Lesson, str], Notes] = DocsNotes
    add_docs_auth: Callable[[str, str, dict[str, str]], None] | None = None
    try:
        import httpx2
    except ImportError:
        return previous
    if script_url or secret_file:
        if not SCRIPT_EXEC_URL.fullmatch(script_url) or not secret_file:
            return previous
        try:
            secret = Path(secret_file).read_text(encoding="ascii").strip()
        except (OSError, ValueError):
            return previous
        if not SECRET.fullmatch(secret):
            return previous

        def script_notes(client: JsonClient, lesson: Lesson, doc_id: str) -> Notes:
            return ScriptDocsNotes(client, lesson, doc_id, script_url, secret)

        docs_factory = script_notes
    else:
        credential_file = os.environ.get("CONNECT_LOG_DUAL_SAVE_GOOGLE_CREDENTIALS", "").strip()
        if not credential_file:
            return previous
        try:
            add_docs_auth = _direct_docs_authorizer(credential_file)
        except (ImportError, SaveUnavailable):
            return previous
    limits = httpx2.Limits(max_connections=200, max_keepalive_connections=40, keepalive_expiry=30.0)
    timeout = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
    sockets = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]
    transport = httpx2.HTTPTransport(http2=True, limits=limits, retries=0, socket_options=sockets)
    with httpx2.Client(transport=transport, timeout=timeout, trust_env=False, follow_redirects=False) as client:
        def request(method: str, url: str, **kwargs: Json) -> Json:
            headers: dict[str, str] = {}
            try:
                if script_url and method == "POST" and url == script_url:
                    response = client.request(method, url, headers=headers, **kwargs)
                    if response.status_code in (302, 303):
                        location = response.headers.get("Location", "")
                        target = urlsplit(location)
                        if (target.scheme, target.netloc, target.path) != (
                            "https", "script.googleusercontent.com", "/macros/echo",
                        ):
                            raise SaveUnavailable()
                        response = client.get(location)
                    response.raise_for_status()
                    return response.json()
                if add_docs_auth is not None and url.startswith(_DOCS):
                    add_docs_auth(method, url, headers)
                elif not url.startswith(_ADMIN + "/api/"):
                    raise SaveUnavailable()
                response = client.request(method, url, headers=headers, **kwargs)
                if response.status_code == 409:
                    raise SaveReview()
                response.raise_for_status()
                return response.json()
            except (httpx2.HTTPError, ValueError):
                raise SaveUnavailable() from None

        bridge = _Transport(request)
        return process_lesson(bridge, bridge, name, day, summary, docs_map, previous, on_target, docs_factory)


def _direct_docs_authorizer(
    credentials_path: str,
) -> Callable[[str, str, dict[str, str]], None]:
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        credentials = Credentials.from_authorized_user_file(credentials_path, scopes=[_SCOPE])
    except (OSError, ValueError, GoogleAuthError):
        raise SaveUnavailable() from None

    def add_auth(method: str, url: str, headers: dict[str, str]) -> None:
        try:
            credentials.before_request(Request(), method, url, headers)
        except GoogleAuthError:
            raise SaveUnavailable() from None

    return add_auth
