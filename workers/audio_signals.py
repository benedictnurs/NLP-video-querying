from __future__ import annotations

import wave
from array import array
from pathlib import Path

from workers.clips import load_clip_records, save_clip_record, save_video_status


def measure_audio_signals(video: dict) -> dict:
    for record in load_clip_records(video["video_id"]):
        signals = audio_signals(Path(record["audio_uri"]))
        record["signals"] = {
            **(record.get("signals") or {}),
            **signals,
            "yelling": signals["loud_speech"],
        }
        models = dict(record.get("model") or {})
        models["audio"] = "rms_baseline"
        record["model"] = models
        save_clip_record(video["video_id"], record)
    return save_video_status(video, "audio_measured")


def audio_signals(audio_path: Path) -> dict:
    samples = _pcm_samples(audio_path)
    if not samples:
        return {"loudness": 0.0, "loud_impact": 0.0, "loud_speech": 0.0}
    peak = max(abs(value) for value in samples) / 32768.0
    mean_sq = sum(value * value for value in samples) / len(samples)
    rms = (mean_sq ** 0.5) / 32768.0
    return {
        "loudness": round(min(rms * 8, 1.0), 3),
        "loud_impact": round(min(peak, 1.0), 3),
        "loud_speech": round(min(rms * 10, 1.0), 3),
    }


def _pcm_samples(path: Path) -> array:
    if not path.exists() or path.stat().st_size < 128:
        return array("h")
    with wave.open(str(path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        samples = array("h")
        samples.frombytes(frames)
        return samples
