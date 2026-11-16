from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

AUDIO_EXTENSIONS: Final = frozenset({
    '.m4a', '.mp3', '.wav', '.flac', '.ogg', '.aac', '.amr', '.3gp',
    '.3ga', '.opus', '.wma', '.m4b', '.aiff', '.aif', '.webm',
})
CONFIG_PATH: Final = Path('data/students/recording_drive_backup.json')


class DriveBackupError(RuntimeError):
    def __init__(self, code: str, *, detail: str = '', stage: str = ''):
        self.code = code
        self.stage = stage
        text = re.sub(r"(?i)\b(?:bearer|basic)\s+[^\s\"']+", '[REDACTED-AUTH]', detail)
        self.detail = re.sub(
            r"(?i)((?:access_token|refresh_token|client_secret|id_token)[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+",
            r'\1[REDACTED]', text,
        )[-2000:]
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class BackupConfig:
    rclone: Path
    remote: str
    folder_id: str
    source_dirs: tuple[Path, ...]
    config_path: Path | None = None


@dataclass(frozen=True, slots=True)
class DriveFile:
    name: str
    md5: str
    size: int


@dataclass(frozen=True, slots=True)
class BackupReport:
    uploaded: int = 0
    duplicates: int = 0
    deferred: int = 0
    failed: int = 0


class RecordingDrive(Protocol):
    def list_files(self) -> tuple[DriveFile, ...]: ...
    def upload(self, directory: Path) -> None: ...


def load_backup_config(base_dir: Path) -> BackupConfig | None:
    path = base_dir / CONFIG_PATH
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DriveBackupError('invalid_config') from exc
    match raw:
        case {'rclone': str(executable), 'remote': str(remote),
              'folder_id': str(folder_id), 'source_dirs': list(directories)}:
            if (not re.fullmatch(r'[A-Za-z0-9_-]+:', remote)
                    or not re.fullmatch(r'[A-Za-z0-9_-]+', folder_id)
                    or not all(isinstance(value, str) for value in directories)):
                raise DriveBackupError('invalid_config')
            config_path = raw.get('rclone_config')
            if config_path is not None and (not isinstance(config_path, str)
                                             or not Path(config_path).expanduser().is_absolute()):
                raise DriveBackupError('invalid_config')
            return BackupConfig(Path(executable), remote, folder_id,
                                tuple(Path(value).expanduser() for value in directories),
                                Path(config_path).expanduser() if config_path is not None else None)
        case _:
            raise DriveBackupError('invalid_config')


class RcloneRecordingDrive:
    def __init__(self, config: BackupConfig):
        self.config = config

    def _run(self, arguments: list[str], *, timeout_seconds: int = 180) -> str:
        config_args: list[str] = []
        if self.config.config_path is not None:
            try:
                with self.config.config_path.open('rb'):
                    config_args = ['--config', str(self.config.config_path)]
            except OSError as exc:
                raise DriveBackupError('drive_config_unavailable', stage=arguments[0], detail=str(exc)) from exc
        try:
            completed = subprocess.run(
                [str(self.config.rclone), *arguments, *config_args, '--drive-root-folder-id', self.config.folder_id],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=timeout_seconds, shell=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise DriveBackupError('drive_network_error', stage=arguments[0],
                                   detail=f'Operation exceeded {timeout_seconds} seconds') from exc
        if completed.returncode:
            error_text = completed.stderr.lower()
            categories = (
                ('drive_config_unavailable', ("didn't find section in config file", 'not found - using defaults')),
                ('drive_storage_full', ('storagequotaexceeded', 'storage quota exceeded')),
                ('drive_rate_limited', ('ratelimitexceeded', 'rate_limit_exceeded', 'dailylimitexceeded',
                                       'quota exceeded for quota metric', 'too many requests', 'error 429')),
                ('drive_network_error', ('timeout', 'connection reset', 'no such host',
                                        'network is unreachable', 'tls handshake', 'connection refused')),
                ('drive_auth_required', ('invalid_grant', 'token has been expired', 'token has expired',
                                        'token has been revoked', 'failed to refresh token',
                                        'invalid_client', 'unauthorized_client')),
                ('drive_permission_denied', ('insufficientfilepermissions', 'insufficientpermissions',
                                            'insufficient authentication scopes', 'access denied', 'permission denied')),
            )
            code = next((code for code, markers in categories if any(m in error_text for m in markers)),
                        f'rclone_exit_{completed.returncode}')
            raise DriveBackupError(code, detail=completed.stderr, stage=arguments[0])
        return completed.stdout

    def list_files(self) -> tuple[DriveFile, ...]:
        try:
            raw = json.loads(self._run(['lsjson', self.config.remote, '--recursive', '--files-only', '--hash']))
        except json.JSONDecodeError as exc:
            raise DriveBackupError('invalid_remote_listing') from exc
        if not isinstance(raw, list):
            raise DriveBackupError('invalid_remote_listing')
        files: list[DriveFile] = []
        for entry in raw:
            match entry:
                case {'Path': str(name), 'Size': int(size), 'Hashes': {'md5': str(md5)}}:
                    if size < 0 or not re.fullmatch('[0-9a-fA-F]{32}', md5):
                        raise DriveBackupError('missing_remote_checksum')
                    files.append(DriveFile(name, md5.lower(), size))
                case _:
                    raise DriveBackupError('missing_remote_checksum')
        return tuple(files)

    def upload(self, directory: Path) -> None:
        self._run(['copy', str(directory), self.config.remote, '--immutable', '--checksum',
                   '--transfers', '5', '--checkers', '8', '--timeout', '60s',
                   '--contimeout', '15s', '--retries', '2'], timeout_seconds=900)


def audio_fingerprint(path: Path) -> tuple[str, str]:
    md5 = hashlib.md5()
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            md5.update(chunk)
            sha.update(chunk)
    return md5.hexdigest(), sha.hexdigest()


def run_recording_backup(
    config: BackupConfig,
    selected_paths: tuple[Path, ...],
    work_dir: Path,
    *,
    drive: RecordingDrive | None = None,
) -> BackupReport:
    provider = drive if drive is not None else RcloneRecordingDrive(config)
    existing = provider.list_files()
    known = {(item.md5, item.size) for item in existing}
    candidates = dict.fromkeys(path.resolve() for path in selected_paths)
    failed = 0
    for directory in config.source_dirs:
        if directory.is_dir():
            try:
                candidates.update(dict.fromkeys(path.resolve() for path in directory.rglob('*')
                                                if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS))
            except OSError:
                failed += 1
    selected = {path.resolve() for path in selected_paths}
    duplicates = deferred = 0
    expected: dict[str, DriveFile] = {}
    work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='recording-backup-', dir=work_dir) as temp:
        stage = Path(temp)
        for source in candidates:
            if source.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            target: Path | None = None
            try:
                before = source.stat()
                if before.st_size == 0 or (source not in selected and time.time() - before.st_mtime < 60):
                    deferred += 1
                    continue
                md5, sha = audio_fingerprint(source)
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    deferred += 1
                    continue
                key = (md5, before.st_size)
                if key in known:
                    duplicates += 1
                    continue
                target = stage / f'{source.stem}__{sha[:24]}{source.suffix.lower()}'
                with source.open('rb') as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                final = source.stat()
                if (audio_fingerprint(target) != (md5, sha)
                        or (before.st_size, before.st_mtime_ns) != (final.st_size, final.st_mtime_ns)):
                    target.unlink()
                    deferred += 1
                    continue
                expected[target.name] = DriveFile(target.name, md5, before.st_size)
                known.add(key)
            except OSError:
                failed += 1
                if target is not None:
                    target.unlink(missing_ok=True)
        uploaded = 0
        if expected:
            provider.upload(stage)
            confirmed = {entry.name: entry for entry in provider.list_files()}
            for name, wanted in expected.items():
                if confirmed.get(name) == wanted:
                    uploaded += 1
                else:
                    failed += 1
    return BackupReport(uploaded, duplicates, deferred, failed)
