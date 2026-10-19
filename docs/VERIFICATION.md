# Verification record

## Counsel Note integration verification (2026-10-19)

HDH source commit `3c6c2383f2cf22a0381520186be22318e4626cd8` was integrated
into the original Counsel Note project. These checks are separate from the
upstream NVIDIA verification below.

- This PC: Windows, Python 3.12.10, Intel Arc 130V; no NVIDIA CUDA device.
- The operational Python environment and Intel/OpenVINO default are preserved.
  The HDH engine is optional; `worker_python` supports a separate speech runtime.
- Isolated QA environment: Python 3.12.10, CPU PyTorch 2.8.0, Streamlit 1.66.0.
  `pytest -q`: **40 passed in 13.43 seconds**; package/test Ruff checks passed.
- Eight legacy check scripts passed: import contracts, log CSV batch state,
  processing history, CSV persistence, CSV worker, five-worker Google Docs
  ordering/recovery, audio batches and runtime stability. Their fake transports
  did not write to real Google Docs or ADMIN services.
- Real browser: the model-missing state disabled transcription; a synthetic
  record allowed speaker labels and notes to be edited and saved. The original
  transcript JSON stayed byte-identical. Desktop layout at 900 px was inspected;
  DOM checks at 1440 px reported no horizontal overflow. The 390 px mobile view
  was inspected and its document width matched its viewport width.
- The browser fixture supplies synthetic transcript evidence, not an ASR result.
  No real speech-model execution, diarization accuracy, counseling accuracy or
  processing-speed improvement on this Intel PC is claimed.
- No real recordings, student mappings, live ADMIN/Docs writes or Apps Script
  deployments were used. Model access terms were not accepted and no access
  tokens or model weights were acquired for this integration.
- LSP remains unavailable because installation was previously declined. Tests,
  Ruff and syntax checks are used instead; no LSP/type-check pass is claimed.
- Earlier source-only end-of-file whitespace warnings remain outside this change.

## Original HDH upstream verification

Date: **2026-10-19**. No real counseling recordings, student registries, Google
Docs writes, ADMIN writes, production servers or paid APIs were used.

## Environment

- Windows x64, Python 3.12.14.
- NVIDIA GeForce RTX 4070 SUPER, 12,282 MiB VRAM, driver 610.88.
- PyTorch 2.8.0+cu128; `torch.cuda.is_available()` returned `True`.
- WhisperX 3.8.6, faster-whisper 1.2.1, CTranslate2 4.8.3,
  pyannote.audio 4.0.7, Streamlit 1.56.0.
- `uv pip check`: all 128 installed packages compatible.
- Full resolved package snapshot: `requirements-validated.txt`.

## Automated checks

Commands ran from the new checkout with its isolated virtual environment:

```powershell
.\.venv\Scripts\python.exe -I -m pytest
.\.venv\Scripts\python.exe -I -m ruff check hdhcounsel tests
.\.venv\Scripts\python.exe -I -m compileall -q hdhcounsel app.py
git diff --check
```

Final result: **34 tests passed in 5.34 seconds**; Ruff, syntax compilation,
whitespace checks and dependency checks exited **0**.

Coverage includes real FFmpeg normalization and invalid input, configuration
validation, missing-model failure before inference, optional word alignment,
speaker preservation, invalid timestamps, model revision pinning, preparation
error redaction, atomic-write failure preservation, immutable evidence versus
review edits, isolated-worker argument/environment/cleanup contracts, worker
error propagation, actual socket denial in a subprocess, stage ordering,
silence behavior, the legacy tuple/timestamp adapter, and Streamlit review save.
ML-stage contract tests substitute model responses; they do not measure ASR or
speaker-clustering accuracy.

An independent read-only review audited upstream tests before running them:

- Complete `check_log_csv_batch_state.py`, `check_log_csv_processing_history.py`
  and `check_csv_persistence.py` passed. The persistence script reported six
  passing cases.
- Ten isolated audio-batch functions passed: upload limits/classification,
  queue order, interrupted/stalled recovery, selective retry, state round-trip,
  missing-source recovery, saved-transcript recovery and temporary-lock recovery.
- Forty legacy application/module/check files parsed successfully.

That baseline used `py -3.12 -I -B` and an audit hook rejecting network,
subprocess and system calls. It did **not** run `check_quick.ps1` wholesale:
that script includes private-config reads, Codex probes, app imports and local
launcher-server actions outside this isolated baseline.

## Actual GPU speech smoke

A **14.213625-second Korean recording** was generated locally with Windows
`Microsoft Heami Desktop`. It contains invented study-plan sentences, not
student data. Both runs used large-v3 / CUDA FP16 / batch 4 and Korean alignment,
with offline environment flags and Python socket audit denial enabled.

- Direct pipeline: **22.55 seconds**, four nonempty segments with timed words.
- Complete isolated service path: passed; **46.73 seconds total** for direct
  and service runs including setup. The second path includes FFmpeg, request
  serialization, `python -I` worker execution and typed result deserialization.
- Example recognized synthetic sentence:
  `오늘은 수학 공부 계획을 이야기하겠습니다.`
- Exact model revisions:
  - `Systran/faster-whisper-large-v3`:
    `edaa852ec7e145841d8ffdb056a99866b5f0a478`
  - `kresnik/wav2vec2-large-xlsr-korean`:
    `629c9a3501c10ba128bf3fa1eebb12af3be03f61`

This is execution evidence, not a counseling accuracy benchmark or production
throughput promise.

## Actual UI check

The new UI ran on dedicated loopback test port 18517. Desktop 1440×1050 and
mobile 390×844 views were inspected, both empty and after transcription.
The synthetic file was uploaded through the real browser, diarization was
explicitly disabled, the real transcription button was clicked, and the saved
four-segment transcript appeared with TXT/JSON downloads and review controls.
Mobile document width equaled viewport width (390 px); no horizontal overflow
or overlapping body controls was observed.

## Not verified / known environment limits

- **Actual Community-1 diarization was not run.** No `HF_TOKEN` was configured
  and the gated model terms require the owner's acceptance. The complete local
  loading and speaker-assignment integration is implemented and contract-tested;
  real clustering accuracy and native runtime execution remain unverified.
  After acquiring the model, run a synthetic two-speaker recording first.
- TorchCodec warned that FFmpeg shared libraries were unavailable. The tested
  path predecodes PCM and passes waveforms, so ASR/alignment succeeded without
  TorchCodec file decoding. Arbitrary other pyannote file-path APIs are not
  validated by this result. The warning was not suppressed.
- Pyannote emitted its normal TF32 reproducibility warning and Lightning
  reported an in-memory legacy VAD checkpoint upgrade. Package assets were
  not rewritten.
- No OS-level outbound firewall was configured; Python socket denial was
  tested. Native-code egress containment is not certified.
- No live legacy Codex summary, Google/ADMIN operation or original full
  operational UI acceptance run was performed.
- The language-server executable was not installed. Ruff, Python compilation,
  tests and actual inference were used; no LSP/type-check pass is claimed.

## Publication checks

Source-only `.gitignore` rules exclude environments, credentials, recordings,
models, caches, runtime data and artifacts. High-confidence GitHub/HF/OpenAI/AWS
token patterns, private-key headers and long literal key/password assignments
were scanned with no matches. This is a targeted pattern scan, not a proof that
every possible secret representation is absent.

GitHub upstream metadata returned `licenseInfo: null`, matching the absence of
a license file. Upstream attribution/history are retained, and the derivative
is published privately pending clarification of redistribution terms.
