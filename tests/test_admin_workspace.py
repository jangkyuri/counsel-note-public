import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from counsel_modules.admin_workspace import (
    WorkspaceAccessError,
    current_worker_workspace,
    use_worker_workspace,
    verified_workspace,
)

SECRET = "synthetic-admin-secret-for-workspace-tests"
NOW = 10000


def sign(principal: str, *, expires: int = NOW + 600) -> str:
    import base64

    identity = base64.urlsafe_b64encode(principal.encode()).decode().rstrip("=")
    payload = f"v1.{expires}.{identity}"
    return f"{payload}.{hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()}"


def test_users_keep_separate_draft_and_job_files_across_new_connections(tmp_path: Path) -> None:
    a, b = (verified_workspace(tmp_path, [sign(name)], SECRET, now=NOW) for name in ("qa-a", "qa-b"))
    assert a.root != b.root
    for relative in ("drafts/current_draft.json", "drafts/audio_batch_queue.json", "drafts/log_csv_to_docs_run.json"):
        legacy = tmp_path / "data" / relative
        first, second = a.path_for(legacy), b.path_for(legacy)
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_text("first user", encoding="utf-8")
        assert not second.exists()
        refreshed_a = verified_workspace(tmp_path, [sign("qa-a", expires=NOW + 800)], SECRET, now=NOW + 3)
        assert refreshed_a.path_for(legacy).read_text(encoding="utf-8") == "first user"
        assert not legacy.exists()


@pytest.mark.parametrize("headers", [
    [],
    ["qa-a"],
    [sign("qa-a"), sign("qa-b")],
    [sign("qa-a", expires=NOW)],
    [sign("qa-a", expires=NOW + 9 * 3600)],
    [sign("qa-a")[:-64] + "0" * 64],
])
def test_invalid_identity_fails_closed(tmp_path: Path, headers: list[str]) -> None:
    with pytest.raises(WorkspaceAccessError):
        verified_workspace(tmp_path, headers, SECRET, now=NOW)
    assert not list(tmp_path.iterdir())


def test_worker_scope_does_not_mix_tasks_or_survive_callback(tmp_path: Path) -> None:
    a, b = (verified_workspace(tmp_path, [sign(name)], SECRET, now=NOW) for name in ("qa-a", "qa-b"))

    def run(workspace):
        assert current_worker_workspace() is None
        with use_worker_workspace(workspace):
            assert current_worker_workspace() == workspace
            return current_worker_workspace().path_for(tmp_path / "data" / "drafts" / "log_csv_worker.lock")

    with ThreadPoolExecutor(max_workers=1) as worker:
        lock_a = worker.submit(run, a).result()
        lock_b = worker.submit(run, b).result()
        assert worker.submit(current_worker_workspace).result() is None
    assert lock_a != lock_b


def test_principal_is_not_used_as_a_path_name(tmp_path: Path) -> None:
    workspace = verified_workspace(tmp_path, [sign("../../qa-user")], SECRET, now=NOW)
    assert workspace.root.parent == tmp_path / "data" / "admin_workspaces"
    assert len(workspace.root.name) == 64
