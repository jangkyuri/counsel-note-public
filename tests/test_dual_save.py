from datetime import date

from counsel_modules.dual_save import Lesson, Outcome, Snapshot, save_missing, select_lesson


class MemoryNotes:
    def __init__(self, text: str, *, lost_reply: bool = False) -> None:
        self.text = text
        self.lost_reply = lost_reply
        self.writes = 0

    def read(self) -> Snapshot:
        return Snapshot(self.text, str(self.writes))

    def append(self, base: Snapshot, addition: str) -> None:
        from counsel_modules.dual_save import SaveUnavailable

        assert self.read() == base
        self.text += addition
        self.writes += 1
        if self.lost_reply:
            raise SaveUnavailable()


def test_selects_only_the_confirmed_lesson() -> None:
    day = date(2026, 11, 22)
    lesson = Lesson("qa-a", "QA Student", day, "Geometry")
    other = Lesson("qa-b", "QA Student", day, "Algebra")
    assert select_lesson("qa student", day, [lesson]) == lesson
    assert select_lesson("qa student", day, [lesson, other]) is None
    assert select_lesson("qa student", date(2026, 11, 23), [lesson]) is None


def test_appends_only_missing_lines_and_is_idempotent() -> None:
    notes = MemoryNotes("QA-KEEP\n- Already present")
    summary = "- Already present\n- QA-ADD"
    assert save_missing(notes, summary) is Outcome.SAVED
    assert save_missing(notes, summary) is Outcome.EXISTING
    assert notes.text.startswith("QA-KEEP\n- Already present")
    assert notes.text.count("Already present") == notes.text.count("QA-ADD") == 1
    assert notes.writes == 1


def test_lost_write_response_is_reconciled_by_readback() -> None:
    notes = MemoryNotes("QA-KEEP", lost_reply=True)
    assert save_missing(notes, "QA-ADD") is Outcome.SAVED
    assert notes.writes == 1
