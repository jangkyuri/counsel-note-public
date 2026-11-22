from __future__ import annotations

import base64
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Protocol

from counsel_modules.dual_save import Lesson, SaveReview, SaveUnavailable, Snapshot, TitleMismatch
from counsel_modules.dual_save_docs import Json, JsonMap, mapping

PROTOCOL = "clg8514-v1"
SCRIPT_EXEC_URL = re.compile(r"https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec")
SECRET = re.compile(r"[a-f0-9]{64}")


class ScriptClient(Protocol):
    def post(self, url: str, *, json: JsonMap) -> Json: ...


@dataclass(frozen=True, slots=True, repr=False)
class ScriptDocsNotes:
    client: ScriptClient
    lesson: Lesson
    doc_id: str
    url: str
    secret: str

    def _request(self, operation: str, base: str = "", addition: str = "") -> JsonMap:
        if not SCRIPT_EXEC_URL.fullmatch(self.url) or not SECRET.fullmatch(self.secret):
            raise SaveUnavailable()
        data = {
            "operation": operation, "student_name": self.lesson.name, "doc_id": self.doc_id,
            "counseling_date": self.lesson.day.isoformat(), "subject": self.lesson.subject,
            "base_revision": base, "addition": addition,
        }
        encoded = base64.urlsafe_b64encode(json.dumps(data, ensure_ascii=False).encode()).decode("ascii")
        stamp = str(int(time.time()))
        message = "\n".join((PROTOCOL, stamp, encoded)).encode("ascii")
        response = self.client.post(self.url, json={
            "action": PROTOCOL, "ts": stamp, "payload": encoded,
            "signature": hmac.digest(self.secret.encode("ascii"), message, "sha256").hex(),
        })
        if not isinstance(response, dict):
            raise SaveUnavailable()
        body = mapping(response)
        if body.get("protocol") != PROTOCOL:
            raise SaveUnavailable()
        if body.get("ok") is True:
            return body
        match body.get("code"):
            case "target_mismatch":
                raise TitleMismatch()
            case "review" | "conflict":
                raise SaveReview()
            case _:
                raise SaveUnavailable()

    def read(self) -> Snapshot:
        body = self._request("read")
        text, revision = body.get("notes"), body.get("revision")
        if not isinstance(text, str) or not isinstance(revision, str) or not SECRET.fullmatch(revision):
            raise SaveUnavailable()
        return Snapshot(text, revision)

    def append(self, base: Snapshot, addition: str) -> None:
        reply = self._request("append", base.revision, addition)
        if not isinstance(reply.get("written"), bool):
            raise SaveUnavailable()
