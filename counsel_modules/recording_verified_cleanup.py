from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Final, TypeAlias

from counsel_modules.audio_batch import AudioBatchJob
from counsel_modules.atomic_json import write_json_atomic
from counsel_modules.log_csv_worker import CsvRunLease
from counsel_modules.recording_cleanup_files import closed_recording, file_signature, plain_recording, recording_paths, stream_fingerprint
from counsel_modules.recording_drive_backup import BackupConfig, RecordingDrive, RcloneRecordingDrive, load_backup_config

POLICY_PATH: Final = Path('data/students/recording_verified_cleanup.json')
STATE_DIR: Final = Path('data/recording_verified_cleanup')
JsonValue: TypeAlias = None | bool | int | float | str | list['JsonValue'] | dict[str, 'JsonValue']


class RecordingCleanupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CleanupCandidate:
    path: str
    signature: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class CleanupApproval:
    audio_path: str
    job_id: str
    md5: str
    sha256: str
    size: int
    cutoff_ns: int
    candidates: tuple[CleanupCandidate, ...]


@dataclass(frozen=True, slots=True)
class CleanupReport:
    recycled: int = 0
    pending: int = 0
    failed: int = 0


def _enabled(base_dir: Path) -> bool:
    path = base_dir / POLICY_PATH
    if not path.is_file():
        return False
    raw = _read_json(path)
    return isinstance(raw, dict) and raw.get('enabled') is True


def _read_json(path: Path) -> JsonValue:
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RecordingCleanupError('invalid_cleanup_state') from exc


def _parse_approval(path: Path) -> CleanupApproval:
    raw = _read_json(path)
    match raw:
        case {'audio_path': str(audio), 'job_id': str(job_id), 'md5': str(md5),
              'sha256': str(sha256), 'size': int(size), 'cutoff_ns': int(cutoff), 'candidates': list(candidates)}:
            if (Path(audio).is_absolute() and re.fullmatch('[a-f0-9]{20}', job_id)
                    and re.fullmatch('[a-f0-9]{32}', md5) and re.fullmatch('[a-f0-9]{64}', sha256)
                    and size > 0 and cutoff > 0 and path.name == f'{job_id}-{sha256[:24]}.json'):
                parsed: list[CleanupCandidate] = []
                for candidate in candidates:
                    match candidate:
                        case {'path': str(value), 'signature': [int(length), int(modified), int(created), int(identity)]}:
                            if Path(value).is_absolute() and length == size and min(modified, created, identity) >= 0:
                                parsed.append(CleanupCandidate(value, (length, modified, created, identity)))
                                continue
                    raise RecordingCleanupError('invalid_cleanup_candidate')
                return CleanupApproval(audio, job_id, md5, sha256, size, cutoff, tuple(parsed))
    raise RecordingCleanupError('invalid_cleanup_approval')


def approve_admin_saved_recording(base_dir: Path, job: AudioBatchJob) -> bool:
    if not _enabled(base_dir):
        return False
    if (job['status'] != 'completed' or not job.get('admin_saved') or not job.get('admin_sid')
            or not job.get('admin_saved_export', '').strip() or not job['summary'].strip()
            or not re.fullmatch('[a-f0-9]{20}', job['job_id'])):
        return False
    transcript = Path(job['transcript_path'])
    if (not transcript.is_absolute() or not transcript.resolve().is_relative_to(base_dir.resolve() / 'data/transcripts')
            or not transcript.is_file() or not transcript.stat().st_size):
        return False
    config = load_backup_config(base_dir)
    path = Path(job['audio_path'])
    if config is None or not plain_recording(path, config.source_dirs):
        return False
    before = file_signature(path)
    with closed_recording(path) as stream:
        md5, sha256 = stream_fingerprint(stream)
    if not before[0] or before != file_signature(path):
        return False
    candidates = []
    for candidate in recording_paths(config.source_dirs):
        signature = file_signature(candidate)
        if signature[0] == before[0]:
            candidates.append(CleanupCandidate(str(candidate), signature))
    approval = CleanupApproval(str(path), job['job_id'], md5, sha256, before[0], time.time_ns(), tuple(candidates))
    destination = base_dir / STATE_DIR / 'approvals' / f'{approval.job_id}-{sha256[:24]}.json'
    if not destination.exists():
        write_json_atomic(destination, asdict(approval))
    return True


def _active_content(base_dir: Path) -> set[str] | None:
    queue = base_dir / 'data/drafts/audio_batch_queue.json'
    if not queue.is_file():
        return set()
    raw = _read_json(queue)
    if not isinstance(raw, dict) or not isinstance(raw.get('jobs'), list):
        return None
    protected: set[str] = set()
    for job in raw['jobs']:
        if not isinstance(job, dict) or job.get('status') not in {'pending', 'processing', 'completed', 'needs_review', 'failed'}:
            return None
        if job['status'] in {'pending', 'processing'}:
            if not isinstance(job.get('audio_path'), str) or not Path(job['audio_path']).is_file():
                return None
            path = Path(job['audio_path'])
            before = file_signature(path)
            with closed_recording(path) as stream:
                protected.add(stream_fingerprint(stream)[1])
            if before != file_signature(path):
                return None
    return protected


def _journal(base_dir: Path, path: Path, approval: CleanupApproval, status: str) -> None:
    with (base_dir / STATE_DIR / 'journal.jsonl').open('a', encoding='utf-8') as stream:
        json.dump({'path': str(path), 'job_id': approval.job_id, 'sha256': approval.sha256,
                   'status': status, 'at_ns': time.time_ns()}, stream)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def _recycle_matches(
    base_dir: Path, config: BackupConfig, approval: CleanupApproval,
    recycle: Callable[[str], None],
) -> CleanupReport:
    recycled = failed = pending = 0
    for candidate in approval.candidates:
        path = Path(candidate.path)
        try:
            if not path.exists() or not plain_recording(path, config.source_dirs):
                continue
            before = file_signature(path)
            if before != candidate.signature:
                continue
            with closed_recording(path) as stream:
                if stream_fingerprint(stream) != (approval.md5, approval.sha256):
                    continue
                active = _active_content(base_dir)
                if active is None or approval.sha256 in active:
                    pending = 1
                    break
                if (before != file_signature(path) or os.fstat(stream.fileno()).st_ino != before[3]
                        or not plain_recording(path, config.source_dirs)):
                    pending = 1
                    continue
                if not _enabled(base_dir):
                    pending = 1
                    break
                _journal(base_dir, path, approval, 'recycle_requested')
                recycle(str(path))
                if path.exists():
                    raise RecordingCleanupError('recycle_not_confirmed')
                recycled += 1
                _journal(base_dir, path, approval, 'recycled')
        except (OSError, RecordingCleanupError):
            failed += 1
    return CleanupReport(recycled, pending or int(failed > 0), failed)


def run_verified_recording_cleanup(
    base_dir: Path, config: BackupConfig, *, drive: RecordingDrive | None = None,
    recycle: Callable[[str], None] | None = None,
) -> CleanupReport:
    if not _enabled(base_dir):
        return CleanupReport()
    directory = base_dir / STATE_DIR
    paths = tuple((directory / 'approvals').glob('*.json'))
    if not paths:
        return CleanupReport()
    with CsvRunLease(directory / 'run.lock'):
        approvals = tuple((path, _parse_approval(path)) for path in paths)
        active = _active_content(base_dir)
        if active is None:
            return CleanupReport(pending=len(approvals))
        provider = drive if drive is not None else RcloneRecordingDrive(config)
        known = {(entry.md5, entry.size) for entry in provider.list_files()}
        if recycle is None:
            from counsel_modules.recording_recycle import recycle_recording
            recycle = recycle_recording
        recycled = pending = failed = 0
        for path, approval in approvals:
            if approval.sha256 in active or (approval.md5, approval.size) not in known:
                pending += 1
                continue
            report = _recycle_matches(base_dir, config, approval, recycle)
            recycled += report.recycled
            failed += report.failed
            pending += report.pending
            if not report.pending:
                receipt = directory / 'receipts' / path.name
                write_json_atomic(receipt, {**asdict(approval), 'recycled': report.recycled, 'at_ns': time.time_ns()})
                path.unlink()
        report = CleanupReport(recycled, pending, failed)
        write_json_atomic(directory / 'status.json', {**asdict(report), 'at_ns': time.time_ns()})
        return report
