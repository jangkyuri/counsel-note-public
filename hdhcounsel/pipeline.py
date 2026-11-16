"""WhisperX stages. This module is invoked only by the offline worker."""

from __future__ import annotations

import gc
import json
import os
import wave
from dataclasses import replace
from pathlib import Path

from .config import CounselError, Settings
from .domain import Transcript


def transcribe_normalized(audio_path: Path, settings: Settings) -> Transcript:
    # Import torch first so its CUDA DLLs are available to CTranslate2 on Windows.
    import numpy as np
    import torch

    dll_handles = []
    if os.name == "nt":
        dll_handles.append(os.add_dll_directory(str(Path(torch.__file__).parent / "lib")))
    try:
        import whisperx

        if settings.device == "cuda" and not torch.cuda.is_available():
            raise CounselError("CUDA is unavailable. Install CUDA-enabled PyTorch or configure CPU/int8.")
        with wave.open(str(audio_path), "rb") as handle:
            if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (16000, 1, 2):
                raise CounselError("The worker requires 16 kHz mono signed 16-bit PCM.")
            audio = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16).astype(np.float32)
        audio /= 32768.0
        duration = len(audio) / 16000
        asr = whisperx.load_model(
            str(settings.models_dir / "asr"),
            settings.device,
            compute_type=settings.compute_type,
            language="ko",
            task="transcribe",
            vad_method="pyannote",
            local_files_only=True,
            asr_options={"beam_size": 5, "condition_on_previous_text": False},
        )
        try:
            result = asr.transcribe(audio, batch_size=settings.batch_size, language="ko")
        finally:
            del asr
            gc.collect()
            if settings.device == "cuda":
                torch.cuda.empty_cache()

        if not result["segments"]:
            return Transcript((), duration, diarized=False)

        aligner, metadata = whisperx.load_align_model(
            "ko",
            settings.device,
            model_name=str(settings.models_dir / "align-ko"),
            model_cache_only=True,
        )
        try:
            result = whisperx.align(
                result["segments"], aligner, metadata, audio, settings.device,
                return_char_alignments=False,
            )
        finally:
            del aligner
            gc.collect()
            if settings.device == "cuda":
                torch.cuda.empty_cache()

        if settings.diarize:
            from whisperx.diarize import DiarizationPipeline

            diarizer = DiarizationPipeline(
                model_name=str(settings.models_dir / "diarization"),
                token=None,
                device=settings.device,
            )
            try:
                turns = diarizer(audio, num_speakers=settings.num_speakers)
                result = whisperx.assign_word_speakers(turns, result, fill_nearest=False)
            finally:
                del diarizer
                gc.collect()
                if settings.device == "cuda":
                    torch.cuda.empty_cache()
        transcript = Transcript.from_whisperx(result, duration, settings.diarize)
        manifest = settings.models_dir / "manifest.json"
        if manifest.exists():
            revisions = json.loads(manifest.read_text(encoding="utf-8"))
            transcript = replace(transcript, model_revisions=revisions)
        return transcript
    finally:
        for handle in dll_handles:
            handle.close()
