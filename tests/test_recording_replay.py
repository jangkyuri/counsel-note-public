from pathlib import Path

import pytest

from counsel_modules.dual_save import Outcome
from counsel_modules.dual_save_runtime import SaveResult
from counsel_modules.recording_replay import (
    RecordingReplay, ReplayConflict, recording_replay_key, replay_key_matches_target,
)


def test_same_audio_and_target_survive_a_new_filename_and_new_store(tmp_path: Path) -> None:
    first, renamed = tmp_path / "first.wav", tmp_path / "renamed.wav"
    first.write_bytes(b"synthetic recording 1")
    renamed.write_bytes(first.read_bytes())
    key = recording_replay_key(first, "QA-SYNTHETIC", "2026-11-22")
    path = tmp_path / "private" / "replay.sqlite3"
    history = RecordingReplay(path)
    history.claim(key, "first summary", "first export")
    history.bind(key, "qa-sid", "qa-doc", "qa-subject")
    history.confirm(key, SaveResult(Outcome.SAVED, Outcome.PENDING, "qa-sid", "qa-doc", "qa-subject"))
    replayed_key = recording_replay_key(renamed, "qa-synthetic", "2026-11-22")
    replayed = RecordingReplay(path).find(replayed_key)
    assert replayed_key == key and replayed is not None
    assert replayed.summary == "first summary" and replayed.export == "first export"
    assert replayed.result.admin.verified and not replayed.result.docs.verified


def test_same_audio_cannot_claim_different_summary_or_change_bound_target(tmp_path: Path) -> None:
    store = RecordingReplay(tmp_path / "replay.sqlite3")
    first = store.claim("key", "first model output", "first export")
    second = store.claim("key", "different model output", "different export")
    assert second == first
    store.bind("key", "first sid", "first doc", "geometry")
    with pytest.raises(ReplayConflict):
        store.bind("key", "other sid", "other doc", "algebra")


def test_bytes_student_date_and_workspace_are_distinct(tmp_path: Path) -> None:
    original, other = tmp_path / "original.wav", tmp_path / "other.wav"
    original.write_bytes(b"one")
    other.write_bytes(b"two")
    key = recording_replay_key(original, "QA", "2026-11-22")
    assert key != recording_replay_key(other, "QA", "2026-11-22")
    assert key != recording_replay_key(original, "QA-2", "2026-11-22")
    assert key != recording_replay_key(original, "QA", "2026-11-23")
    assert replay_key_matches_target(key, "qa", "2026-11-22")
    assert not replay_key_matches_target(key, "qa", "2026-11-23")
    RecordingReplay(tmp_path / "browser-a" / "replay.sqlite3").claim(key, "private summary", "export")
    assert RecordingReplay(tmp_path / "browser-b" / "replay.sqlite3").find(key) is None
