from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ASSERTION_HEADER: Final = "X-Connect-Log-Admin-Assertion"
MAX_ASSERTION_SECONDS: Final = 8 * 3600
ASSERTION: Final = re.compile(r"v1\.([0-9]{1,12})\.([A-Za-z0-9_-]{1,256})\.([0-9a-f]{64})")


class WorkspaceAccessError(RuntimeError):
    """The hosted app has no usable verified ADMIN workspace."""


@dataclass(frozen=True, slots=True)
class AdminWorkspace:
    base: Path
    root: Path

    def path_for(self, legacy_path: Path) -> Path:
        relative = legacy_path.relative_to(self.base)
        scoped = self.root / relative
        if not scoped.resolve().is_relative_to(self.root) or self.root.resolve() != self.root:
            raise WorkspaceAccessError("Invalid ADMIN workspace storage path")
        return scoped


_worker_workspace: ContextVar[AdminWorkspace | None] = ContextVar("connect_log_admin_workspace", default=None)


def hosted_mode_enabled() -> bool:
    return os.environ.get("CONNECT_LOG_MULTI_USER", "").strip() not in {"", "0"}


def verified_workspace(
    base: Path,
    headers: Sequence[str],
    secret: str,
    *,
    now: int | None = None,
) -> AdminWorkspace:
    """Resolve a stable workspace only after checking the ADMIN proxy assertion."""
    invalid = "Missing or invalid ADMIN proxy assertion"
    key = secret.encode("utf-8")
    if len(key) < 32 or len(headers) != 1:
        raise WorkspaceAccessError(invalid)
    value = headers[0]
    match = ASSERTION.fullmatch(value)
    if not match:
        raise WorkspaceAccessError(invalid)
    expiry, encoded, mac = match.groups()
    unsigned = value.rsplit(".", 1)[0]
    expected = hmac.new(key, unsigned.encode("ascii"), hashlib.sha256).hexdigest()
    current = int(time.time()) if now is None else now
    if not hmac.compare_digest(expected, mac) or not current < int(expiry) <= current + MAX_ASSERTION_SECONDS:
        raise WorkspaceAccessError(invalid)
    try:
        principal = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        raise WorkspaceAccessError(invalid) from None
    if not principal or principal != principal.strip() or not principal.isprintable():
        raise WorkspaceAccessError(invalid)
    opaque_id = hashlib.sha256(("connect-log-admin-user-v1:" + principal).encode("utf-8")).hexdigest()
    base = base.resolve()
    return AdminWorkspace(base, base / "data" / "admin_workspaces" / opaque_id)


def current_worker_workspace() -> AdminWorkspace | None:
    return _worker_workspace.get()


@contextmanager
def use_worker_workspace(workspace: AdminWorkspace | None) -> Iterator[None]:
    token = _worker_workspace.set(workspace)
    try:
        yield
    finally:
        _worker_workspace.reset(token)
