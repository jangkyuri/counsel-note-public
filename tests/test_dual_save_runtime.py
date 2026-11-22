from __future__ import annotations

from datetime import date

import pytest

from counsel_modules.dual_save import Outcome, SaveReview, SaveUnavailable
from counsel_modules.dual_save_docs import Json, JsonMap, document_snapshot
from counsel_modules.dual_save_runtime import SaveResult, process_lesson

DAY = date(2026, 11, 22)
DOC = "qa-synthetic-doc-00001"
SUBJECT = "\uacf5\uc2182"
NAME = "QA Student"
PENDING = SaveResult(Outcome.PENDING, Outcome.PENDING)


def body(title: str, label: str, notes: str, revision: str) -> JsonMap:
    def cell(text: str, end: int) -> JsonMap:
        return {"content": [{"endIndex": end, "paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]}}]}

    return {
        "title": title, "revisionId": revision,
        "tabs": [{"tabProperties": {"tabId": "qa-tab"},
                  "documentTab": {"body": {"content": [{"table": {"tableRows": [
                      {"tableCells": [cell("\uc0c1\ub2f4\uc77c\uc790", 6), cell("\uc138\ubd80\ub0b4\uc6a9", 15)]},
                      {"tableCells": [cell(label, 50), cell(notes, 150)]},
                      {"tableCells": [cell("", 160), cell("", 170)]},
                  ]}}]}}}],
    }


class FakeServices:
    def __init__(self) -> None:
        self.admin = "QA-KEEP\n- Already present"
        self.docs = self.admin
        self.admin_writes = 0
        self.docs_writes = 0
        self.title = NAME + " Record"
        self.label = DAY.isoformat() + "\n\uacfc\ubaa9: \uacf5\ud1b5\uc218\ud5592"
        self.docs_offline = False
        self.lost_docs_response = False
        self.duplicate_daily_candidate = False
        self.fail_admin_conflict = False

    def get(self, url: str, *, params: dict[str, str] | None = None) -> Json:
        if url.endswith("/api/attend"):
            assert params == {"date": DAY.isoformat()}
            return [
                {"id": "one", "name": NAME, "curriculum": "\uacf5\uc2182 APEX"},
                {"id": "two", "name": NAME, "curriculum": "\uae30\ud558"},
            ]
        if url.endswith("/api/attendance"):
            return {"one": 1, "two": int(self.duplicate_daily_candidate)}
        if url.endswith("/api/logs/entry"):
            assert params == {"date": DAY.isoformat(), "sid": "one"}
            return {"ok": True, "entry": {"notes": self.admin}}
        assert url == f"https://docs.googleapis.com/v1/documents/{DOC}"
        if self.docs_offline:
            raise SaveUnavailable()
        return body(self.title, self.label, self.docs, str(self.docs_writes))

    def post(self, url: str, *, json: JsonMap) -> Json:
        if url.endswith("/api/logs/patch"):
            assert json["sid"] == "one" and json["date"] == DAY.isoformat()
            assert json["base"] == {"notes": self.admin}
            if self.fail_admin_conflict:
                self.admin += "\nConcurrent QA edit"
                raise SaveReview()
            entry = json["entry"]
            assert isinstance(entry, dict) and isinstance(text := entry["notes"], str)
            self.admin = text
            self.admin_writes += 1
            return {"ok": True}
        assert url == f"https://docs.googleapis.com/v1/documents/{DOC}:batchUpdate"
        assert json["writeControl"] == {"requiredRevisionId": str(self.docs_writes)}
        assert json["requests"] == [{
            "insertText": {"location": {"index": 149, "tabId": "qa-tab"}, "text": "\n\n- QA-ADD"},
        }]
        self.docs += "\n\n- QA-ADD"
        self.docs_writes += 1
        if self.lost_docs_response:
            raise SaveUnavailable()
        return {}

    def save(self, previous: SaveResult = PENDING) -> SaveResult:
        return process_lesson(
            self, self, NAME, DAY, "- Already present\n- QA-ADD", [(NAME, DOC)], previous,
        )


def test_same_record_twice_preserves_both_destinations() -> None:
    services = FakeServices()
    first = services.save()
    rerun_without_checkpoint = services.save()
    assert first.admin is first.docs is Outcome.SAVED
    assert rerun_without_checkpoint.admin is rerun_without_checkpoint.docs is Outcome.EXISTING
    assert services.admin_writes == services.docs_writes == 1
    assert services.admin == services.docs == "QA-KEEP\n- Already present\n\n- QA-ADD"


def test_retry_skips_admin_when_docs_was_offline() -> None:
    services = FakeServices()
    services.docs_offline = True
    first = services.save()
    assert first.admin is Outcome.SAVED and first.docs is Outcome.PENDING
    services.docs_offline = False
    second = services.save(first)
    assert second.admin is second.docs is Outcome.SAVED
    assert services.admin_writes == services.docs_writes == 1


def test_reconciles_docs_when_the_write_response_is_lost() -> None:
    services = FakeServices()
    services.lost_docs_response = True
    result = services.save()
    assert result.docs is Outcome.SAVED and services.docs_writes == 1


def test_leaves_existing_yearless_doc_record_untouched() -> None:
    services = FakeServices()
    services.label = "11/22"
    result = services.save()
    assert result.admin is Outcome.SAVED and result.docs is Outcome.REVIEW
    assert services.docs_writes == 0


def test_rejects_ambiguous_daily_roster_and_title_mismatch() -> None:
    services = FakeServices()
    services.duplicate_daily_candidate = True
    assert services.save().admin is Outcome.REVIEW
    services.duplicate_daily_candidate = False
    services.title = "Other Student Record"
    result = services.save()
    assert result.admin is result.docs is Outcome.REVIEW
    assert services.admin_writes == services.docs_writes == 0


def test_keeps_concurrent_admin_edit() -> None:
    services = FakeServices()
    services.fail_admin_conflict = True
    result = services.save()
    assert result.admin is Outcome.REVIEW and result.docs is Outcome.SAVED
    assert services.admin.endswith("Concurrent QA edit")
    assert services.admin_writes == 0


def test_different_subject_does_not_reuse_row() -> None:
    doc = body(NAME, DAY.isoformat() + "\n\uacfc\ubaa9: \uae30\ud558", "QA-KEEP", "rev")
    snapshot = document_snapshot(doc, DAY, SUBJECT)
    assert snapshot.text == "" and snapshot.date_index == 159 and snapshot.index == 169
    with pytest.raises(SaveReview):
        document_snapshot(body(NAME, "11/22", "QA-KEEP", "rev"), DAY, SUBJECT)
