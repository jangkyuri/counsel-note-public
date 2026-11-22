import base64
import hmac
import json as json_lib
from datetime import date

import pytest

from counsel_modules.dual_save import Lesson, Outcome, SaveUnavailable, save_missing
from counsel_modules.dual_save_docs import JsonMap
from counsel_modules.dual_save_script import PROTOCOL, ScriptDocsNotes

KEY = "0" * 64
URL = "https://script.google.com/macros/s/qa-synthetic-exec/exec"
DOC = "qa-synthetic-doc-8514"
LESSON = Lesson("qa", "QA-SYNTHETIC", date(2026, 11, 22), "\uacf5\uc2182")


class FakeScript:
    def __init__(self) -> None:
        self.notes = "QA-KEEP\n- already in document"
        self.writes = 0
        self.reads = 0

    def post(self, url: str, *, json: JsonMap) -> JsonMap:
        assert url == URL
        assert set(json) == {"action", "ts", "payload", "signature"}
        assert json["action"] == PROTOCOL
        message = "\n".join(str(json[k]) for k in ("action", "ts", "payload")).encode("ascii")
        assert json["signature"] == hmac.digest(KEY.encode("ascii"), message, "sha256").hex()
        packet = json_lib.loads(base64.urlsafe_b64decode(str(json["payload"])))
        assert packet["doc_id"] == DOC and packet["counseling_date"] == "2026-11-22"
        if packet["operation"] == "read":
            self.reads += 1
            return {"protocol": PROTOCOL, "ok": True, "notes": self.notes, "revision": f"{self.writes:064x}"}
        assert packet["base_revision"] == f"{self.writes:064x}"
        assert packet["addition"] == "\n\n- QA-ADD"
        self.notes += packet["addition"]
        self.writes += 1
        return {"protocol": PROTOCOL, "ok": True, "written": True}


def test_signed_append_uses_actual_readback_and_skips_repeat() -> None:
    server = FakeScript()
    target = ScriptDocsNotes(server, LESSON, DOC, URL, KEY)
    summary = "- already in document\n- QA-ADD"
    assert save_missing(target, summary) is Outcome.SAVED
    assert save_missing(target, summary) is Outcome.EXISTING
    assert server.writes == 1 and server.reads == 3
    assert server.notes.count("QA-KEEP") == server.notes.count("QA-ADD") == 1


def test_legacy_success_response_cannot_be_used_as_8514_readback() -> None:
    class LegacyServer:
        def post(self, url: str, *, json: JsonMap) -> JsonMap:
            return {"ok": True, "found_existing": True}

    target = ScriptDocsNotes(LegacyServer(), LESSON, DOC, URL, KEY)
    with pytest.raises(SaveUnavailable):
        target.read()
    assert save_missing(target, "QA-ADD") is Outcome.PENDING
