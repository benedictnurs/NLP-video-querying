from __future__ import annotations

import os
from pathlib import Path

from workers.paths import models_dir


def load_whisper():
    from faster_whisper import WhisperModel

    os.environ.setdefault("HF_HOME", str(models_dir() / "huggingface"))
    model_name = os.environ.get("WHISPER_MODEL", "Systran/faster-whisper-tiny.en")
    return WhisperModel(model_name, device="cpu", compute_type="int8")


def transcribe_wav(model, audio_path: Path) -> tuple[str, list[dict], str | None]:
    if model is None:
        return "", [], "whisper_unavailable"
    if not audio_path.exists() or audio_path.stat().st_size < 128:
        return "", [], f"missing_audio:{audio_path}"
    try:
        segments, _info = model.transcribe(str(audio_path), vad_filter=True, language="en")
        rows = []
        texts = []
        for segment in segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            texts.append(text)
            rows.append(
                {
                    "start_ms": int(round(float(segment.start) * 1000)),
                    "end_ms": int(round(float(segment.end) * 1000)),
                    "text": text,
                }
            )
        return " ".join(texts).strip(), rows, None
    except Exception as exc:
        return "", [], str(exc)[:500]
