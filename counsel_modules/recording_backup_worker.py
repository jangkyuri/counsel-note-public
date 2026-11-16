from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Final

from counsel_modules.atomic_json import write_json_atomic
from counsel_modules.audio_batch import AudioBatchJob
from counsel_modules.log_csv_worker import CsvRunBusyError, CsvRunLease, csv_worker_is_running
from counsel_modules.recording_drive_backup import (
    BackupReport,
    BackupConfig,
    DriveBackupError,
    load_backup_config,
    run_recording_backup,
)
from counsel_modules.recording_verified_cleanup import (
    STATE_DIR as CLEANUP_STATE_DIR, RecordingCleanupError, approve_admin_saved_recording,
    run_verified_recording_cleanup,
)

STATUS_PATH: Final = Path('data/recording_drive_backup/status.json')
BACKUP_WARNING: Final = '녹음 Drive 백업이 완료되지 않았습니다. 원본은 보존되며 다음 녹음 처리 때 다시 시도합니다.'
LOGGER: Final = logging.getLogger(__name__)


def _cleanup_after_backup(base_dir: Path, config: BackupConfig, paths: tuple[Path, ...], report: BackupReport) -> BackupReport:
    try:
        cleanup = run_verified_recording_cleanup(base_dir, config)
    except (OSError, DriveBackupError, RecordingCleanupError, CsvRunBusyError) as exc:
        LOGGER.warning('Verified recording cleanup deferred: %s', type(exc).__name__)
        write_json_atomic(base_dir / CLEANUP_STATE_DIR / 'status.json',
                          {'failed': 1, 'error': type(exc).__name__, 'updated_at': datetime.now(timezone.utc).isoformat()})
        return report
    if cleanup.recycled:
        LOGGER.info('Verified recordings recycled: %s; pending: %s', cleanup.recycled, cleanup.pending)
        checked = run_recording_backup(config, tuple(path for path in paths if path.exists()), base_dir / 'data/tmp')
        return BackupReport(report.uploaded + checked.uploaded, checked.duplicates, checked.deferred, checked.failed)
    return report


def _publish(base_dir: Path, status: str, report: BackupReport, error: str = '', *,
             error_detail: str = '', error_stage: str = '') -> None:
    value = {
        'status': status, **asdict(report), 'error': error,
        'error_detail': error_detail, 'error_stage': error_stage,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    if error:
        write_json_atomic(base_dir / STATUS_PATH.with_name('last_error.json'), value)
    write_json_atomic(base_dir / STATUS_PATH, value)


class _RecordingBackupWorker:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.lock = threading.Lock()
        self.pending: set[Path] = set()
        self.scan_requested = False
        self.running = False
        self.idle = threading.Event()
        self.idle.set()

    def _launch_locked(self) -> None:
        self.running = True
        self.idle.clear()
        thread = threading.Thread(target=self._run, name='connect-log-recording-backup', daemon=True)
        try:
            thread.start()
        except RuntimeError:
            self.running = False
            self.idle.set()
            raise

    def request(self, audio_path: Path | None) -> None:
        with self.lock:
            self.scan_requested = True
            if audio_path is not None:
                self.pending.add(audio_path.resolve())
            if not self.running:
                self._launch_locked()

    def _run(self) -> None:
        try:
            while True:
                with self.lock:
                    paths = tuple(self.pending)
                    self.pending.clear()
                    scan_requested = self.scan_requested
                    self.scan_requested = False
                if not paths and not scan_requested:
                    return
                try:
                    config = load_backup_config(self.base_dir)
                    if config is None:
                        return
                    with CsvRunLease(self.base_dir / 'data/recording_drive_backup/run.lock'):
                        for attempt in range(1, 4):
                            _publish(self.base_dir, 'running' if attempt == 1 else 'retrying', BackupReport())
                            error = ''
                            error_detail = error_stage = ''
                            try:
                                report = run_recording_backup(config, paths, self.base_dir / 'data/tmp')
                                if not report.failed:
                                    report = _cleanup_after_backup(self.base_dir, config, paths, report)
                            except (OSError, DriveBackupError, subprocess.SubprocessError) as exc:
                                error = exc.code if isinstance(exc, DriveBackupError) else type(exc).__name__
                                if isinstance(exc, DriveBackupError):
                                    error_detail, error_stage = exc.detail, exc.stage
                                LOGGER.warning('Recording backup attempt %s failed: %s', attempt, error)
                                report = BackupReport(failed=1)
                            if not report.failed or attempt == 3 or error in {
                                'drive_auth_required', 'drive_permission_denied', 'drive_storage_full',
                                'drive_config_unavailable', 'invalid_config',
                            }:
                                status = 'failed' if report.failed else 'pending' if report.deferred else 'completed'
                                _publish(self.base_dir, status, report, error,
                                         error_detail=error_detail, error_stage=error_stage)
                                break
                            _publish(self.base_dir, 'retrying', report, error,
                                     error_detail=error_detail, error_stage=error_stage)
                            delay = attempt * 60 if error == 'drive_rate_limited' else attempt * 2
                            if error == 'drive_network_error':
                                delay = (10, 30)[attempt - 1]
                            time.sleep(delay)
                except CsvRunBusyError:
                    LOGGER.info('Recording backup already active in another app process')
                except (OSError, DriveBackupError, subprocess.SubprocessError) as exc:
                    LOGGER.warning('Recording backup failed: %s', type(exc).__name__)
                    _publish(self.base_dir, 'failed', BackupReport(failed=1),
                             exc.code if isinstance(exc, DriveBackupError) else type(exc).__name__,
                             error_detail=exc.detail if isinstance(exc, DriveBackupError) else '',
                             error_stage=exc.stage if isinstance(exc, DriveBackupError) else '')
        finally:
            with self.lock:
                self.running = False
                if self.pending or self.scan_requested:
                    self._launch_locked()
                else:
                    self.idle.set()


@lru_cache(maxsize=4)
def backup_worker(base_dir: Path) -> _RecordingBackupWorker:
    return _RecordingBackupWorker(base_dir)


def request_recording_backup(base_dir: Path, audio_path: Path | None) -> None:
    try:
        if load_backup_config(base_dir) is not None:
            backup_worker(base_dir.resolve()).request(audio_path)
        elif audio_path is None:
            _publish(base_dir, 'failed', BackupReport(failed=1), 'drive_config_unavailable')
    except (OSError, DriveBackupError, RuntimeError) as exc:
        LOGGER.warning('Recording backup could not start: %s', type(exc).__name__)
        try:
            _publish(base_dir, 'failed', BackupReport(failed=1),
                     exc.code if isinstance(exc, DriveBackupError) else type(exc).__name__)
        except OSError:
            LOGGER.warning('Recording backup status could not be saved')


def retry_recording_backup(base_dir: Path) -> None:
    request_recording_backup(base_dir, None)


def request_recording_cleanup(base_dir: Path, job: AudioBatchJob) -> None:
    try:
        if approve_admin_saved_recording(base_dir, job):
            backup_worker(base_dir.resolve()).request(None)
    except (OSError, DriveBackupError, RecordingCleanupError, RuntimeError) as exc:
        LOGGER.warning('Verified recording cleanup approval deferred: %s', type(exc).__name__)


def recording_backup_is_running(base_dir: Path) -> bool:
    return (backup_worker(base_dir.resolve()).running
            or csv_worker_is_running(base_dir / 'data/recording_drive_backup/run.lock'))


def recording_backup_warning(base_dir: Path) -> str:
    path = base_dir / STATUS_PATH
    if not path.is_file():
        return ''
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return BACKUP_WARNING
    if not isinstance(raw, dict):
        return BACKUP_WARNING
    if raw.get('status') in {'running', 'retrying'} and not recording_backup_is_running(base_dir):
        return '녹음 Drive 백업이 중단됐습니다 · 백업 다시 시도 가능 · 원본 보존 중'
    match raw:
        case {'status': 'failed', 'error': 'drive_config_unavailable' | 'invalid_config'}:
            return 'Drive 연결 설정을 읽을 수 없어 백업 미완료 · 원본 보존 중'
        case {'status': 'retrying', 'error': 'drive_rate_limited'}:
            return 'Drive 요청 제한 · 자동 재시도 대기 중 · 원본 보존 중'
        case {'status': 'failed', 'error': 'drive_rate_limited'}:
            return 'Drive 요청 제한으로 백업 미완료 · 전용 연결 확인 필요 · 원본 보존 중'
        case {'status': 'failed', 'error': 'drive_auth_required'}:
            return 'Drive 로그인 연결 만료 · 재연결 필요 · 원본 보존 중'
        case {'status': 'failed', 'error': 'drive_permission_denied'}:
            return '녹음 파일 또는 Drive 폴더 접근 권한 오류 · 원본 보존 중'
        case {'status': 'failed', 'error': 'drive_storage_full'}:
            return 'Drive 저장 공간 부족으로 백업 미완료 · 원본 보존 중'
        case {'status': 'failed', 'error': 'drive_network_error'}:
            return 'Drive 연결 오류로 백업 미완료 · 원본 보존 중'
        case {'status': 'running' | 'retrying'}:
            return '녹음 Drive 백업 진행 중 · 원본 보존 중'
        case {'status': 'pending'}:
            return '변경 중인 녹음 파일 백업 대기 · 원본 보존 중'
        case {'status': 'failed'}:
            return BACKUP_WARNING
        case _:
            return ''
