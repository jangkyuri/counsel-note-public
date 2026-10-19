# Architecture and operations

## Boundaries

`config.py` validates immutable settings. `domain.py` defines the JSON-compatible
`Transcript`, `Segment` and `Word` contracts. Missing word timestamps and speaker
labels stay missing; they are not fabricated.

`service.py` normalizes an input with `audio.py`, starts a fresh Python `-I`
worker, and validates its success envelope. Temporary upload/PCM/request/result
files are deleted on success, failure and timeout. `worker.py` sets offline and
telemetry flags before loading any ML libraries and denies Python socket
connect/DNS/sendto audit events. This is defense in depth, **not an operating
system firewall guarantee for arbitrary native code**. For strict isolation,
add an outbound firewall rule for the dedicated Python executable after model
preparation and validate it with synthetic audio.

The optional `worker_python` setting selects a separate installed speech Python
environment without replacing the operational app's interpreter or packages.
Omitting it preserves the current interpreter. Relative interpreter paths are
resolved against the configuration file, not the temporary worker directory.
Install HDHcounsel and its speech dependencies in that environment before use.

`pipeline.py` runs ASR, alignment and optional diarization sequentially. Each
model is deleted before the next stage; the process boundary releases native
allocations after each recording. The same 16 kHz mono waveform is supplied to
all stages, avoiding TorchCodec file decoding. `storage.py` atomically saves
immutable transcript evidence and separate editable review JSON.

`ui.py` and `cli.py` depend on these boundaries, not on the legacy app's private
configuration. The local UI binds only to loopback and disables Streamlit usage
statistics. Nothing automatically invokes Codex, ADMIN or Google Docs.

## Data and recovery

Default root: `~/.hdhcounsel`; override with `HDHCOUNSEL_HOME`.

```text
models/
  asr/                  CTranslate2 large-v3, including tokenizer.json
  align-ko/             Korean wav2vec2 weights and processor metadata
  diarization/          complete Community-1 snapshot, including submodels
  nltk_data/            punkt_tab, including English tables used for Korean
  manifest.json         acquired model commit revisions
records/
  <UTC time>-<random ID>/
    transcript.json     original timed, attributed evidence + student metadata
    review.json         human notes, confirmed speaker names, review status
config.toml             optional settings; never a credential store
```

Review state persists when saved, but v1 has no historical-record browser;
records remain ordinary UTF-8 JSON files. Keep a private backup of this folder.
The app does not encrypt local records. Use your normal OS disk encryption and
access controls. No real student records or model weights belong in Git.

The input source is never modified or deleted. Stereo audio is averaged into
mono: if channels are known to contain separate speakers, do not treat this
pipeline as channel-separated transcription. Speaker labels do not identify
people and are not stable between recordings. Silence produces an empty result,
not invented counseling notes.

## Model decision and reproducibility

Research date: 2026-10-19. WhisperX **3.8.6 is stable**; 3.8.7rc1 is a prerelease.
We keep the requested large-v3 baseline rather than claiming it is the newest
ASR architecture. Qwen3-ASR-1.7B is a newer Korean-capable comparison candidate,
but is not a drop-in WhisperX backend, and no local counseling benchmark proves
it better for this use case. Model quality claims require a representative,
consented evaluation set; a synthetic smoke proves execution only.

`prepare` resolves explicit model commit IDs and records them after download.
Re-running preparation may select newer commits: retain the manifest and model
folder when reproducing a run. Full dependency pins for the verified Windows
environment are in `requirements-validated.txt`. CUDA PyTorch wheels must come
from the official `cu128` index, not an arbitrary package mirror.

Community-1 requires its Hugging Face access terms and a read-only `HF_TOKEN`
during preparation. Without that access, use `prepare --no-diarization` and
explicitly disable diarization for inference. We do not silently downgrade a
requested diarized job to unattributed text.

## Windows runtime troubleshooting

- Python 3.12 x64 is the tested interpreter. WhisperX excludes Python 3.14.
- Torch 2.8 / torchaudio 2.8 / torchvision 0.23 and CUDA 12.8 are the selected
  runtime. CTranslate2 also needs compatible cuBLAS and cuDNN 9 DLLs; the worker
  adds PyTorch's `torch/lib` directory to the Windows DLL search path.
- `imageio-ffmpeg` provides the decoding executable. Pyannote may warn about
  missing TorchCodec FFmpeg shared DLLs. The current waveform-based path does not
  use TorchCodec decoding; do not infer that other pyannote file-path APIs work.
- CUDA out of memory: set `batch_size=1`, then `int8_float16`, or use CPU/int8.
  The app does not silently change model/device after a failure.
- Missing assets: prepare online before opening counseling audio. Korean
  alignment requires NLTK's **punkt_tab**, not just the older punkt package.
- No full diarization smoke without gated weights is claimed. Obtain the model
  and run a synthetic two-speaker recording before relying on attribution.

## Primary references

- https://github.com/m-bain/whisperX/tree/v3.8.6
- https://pypi.org/project/whisperx/3.8.6/
- https://raw.githubusercontent.com/m-bain/whisperX/v3.8.6/whisperx/asr.py
- https://raw.githubusercontent.com/m-bain/whisperX/v3.8.6/whisperx/alignment.py
- https://raw.githubusercontent.com/m-bain/whisperX/v3.8.6/whisperx/diarize.py
- https://huggingface.co/Systran/faster-whisper-large-v3
- https://huggingface.co/kresnik/wav2vec2-large-xlsr-korean
- https://huggingface.co/pyannote/speaker-diarization-community-1
- https://github.com/QwenLM/Qwen3-ASR
- https://pytorch.org/get-started/previous-versions/
- https://github.com/SYSTRAN/faster-whisper
