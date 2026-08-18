from __future__ import annotations

import os
from pathlib import Path

from workers.clips import load_clip_records, save_clip_record, save_video_status
from workers.metadata import keyword_hits, repeated_commands
from workers.paths import models_dir


def transcribe_video_clips(video: dict) -> dict:
    model = load_whisper()
    asr_name = os.environ.get("WHISPER_MODEL", "Systran/faster-whisper-tiny.en")
    for record in load_clip_records(video["video_id"]):
        transcript, segments, error = transcribe_wav(model, Path(record["audio_uri"]))
        models = dict(record.get("model") or {})
        models.update({"asr": f"faster-whisper/{asr_name}", "vad": "silero"})
        record["transcript"] = transcript
        record["transcript_segments"] = segments
        if error:
            record["asr_error"] = error
        record["keyword_hits"] = keyword_hits(transcript)
        record["signals"] = {
            **(record.get("signals") or {}),
            "repeated_commands": repeated_commands(transcript),
        }
        record["model"] = models
        save_clip_record(video["video_id"], record)
    return save_video_status(video, "transcribed")


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
