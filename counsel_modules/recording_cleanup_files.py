from __future__ import annotations

import ctypes
import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from counsel_modules.recording_drive_backup import AUDIO_EXTENSIONS


def file_signature(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino


def plain_recording(path: Path, roots: tuple[Path, ...]) -> bool:
    if not path.is_absolute() or '..' in path.parts or path.suffix.lower() not in AUDIO_EXTENSIONS:
        return False
    for root in roots:
        if not path.is_relative_to(root):
            continue
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                return False
            if part == root:
                return stat.S_ISREG(path.lstat().st_mode)
    return False


def _raise_walk_error(error: OSError) -> None:
    raise error


def recording_paths(roots: tuple[Path, ...]) -> Iterator[Path]:
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir() or root.is_symlink() or getattr(root.stat(), 'st_file_attributes', 0) & 0x400:
            continue
        for parent, directories, files in os.walk(root, followlinks=False, onerror=_raise_walk_error):
            directory = Path(parent)
            directories[:] = [name for name in directories
                              if not getattr((directory / name).lstat(), 'st_file_attributes', 0) & 0x400
                              and not (directory / name).is_symlink()]
            for name in files:
                path = directory / name
                if path not in seen and plain_recording(path, roots):
                    seen.add(path)
                    yield path


@contextmanager
def closed_recording(path: Path) -> Iterator[BinaryIO]:
    if os.name != 'nt':
        with path.open('rb') as stream:
            yield stream
        return
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # Block writers, but permit the recycle operation while the verified read handle is open.
    handle = create(str(path), 0x80000000, 1 | 4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), 'Recording is still in use')
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close(handle)
        raise
    with os.fdopen(descriptor, 'rb') as stream:
        yield stream


def stream_fingerprint(stream: BinaryIO) -> tuple[str, str]:
    md5, sha256 = hashlib.md5(), hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        md5.update(chunk)
        sha256.update(chunk)
    return md5.hexdigest(), sha256.hexdigest()
