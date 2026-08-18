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
        models["audio"] = "rms_windows"
        record["model"] = models
        save_clip_record(video["video_id"], record)
    return save_video_status(video, "audio_measured")


def audio_signals(audio_path: Path) -> dict:
    samples, rate = _pcm_samples(audio_path)
    if not samples:
        return {"loudness": 0.0, "loud_impact": 0.0, "loud_speech": 0.0, "impact_at_ms": 0}
    window = max(int(rate * 0.05), 160)
    rms = []
    for start in range(0, len(samples) - window, window):
        chunk = samples[start : start + window]
        mean_sq = sum(value * value for value in chunk) / len(chunk)
        rms.append((mean_sq ** 0.5) / 32768.0)
    if not rms:
        return {"loudness": 0.0, "loud_impact": 0.0, "loud_speech": 0.0, "impact_at_ms": 0}
    ranked = sorted(rms)
    median = ranked[len(ranked) // 2]
    peak = ranked[-1]
    floor = max(median, 1e-4)
    spike = peak / floor
    # A bang is a short window far above the clip's own median, not "audio exists".
    loud_impact = round(min(max((spike - 4.0) / 8.0, 0.0), 1.0), 3)
    high = [value >= max(median * 4, 0.08) for value in rms]
    longest = _longest_run(high)
    seconds_loud = longest * (window / rate)
    loud_speech = round(min(seconds_loud / 2.0, 1.0), 3)
    mean_sq = sum(value * value for value in samples) / len(samples)
    overall = (mean_sq ** 0.5) / 32768.0
    peak_index = rms.index(peak)
    impact_at_ms = int(peak_index * window / rate * 1000)
    return {
        "loudness": round(min(overall * 8, 1.0), 3),
        "loud_impact": loud_impact,
        "loud_speech": loud_speech,
        "impact_at_ms": impact_at_ms,
    }


def _longest_run(flags: list[bool]) -> int:
    best = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        if current > best:
            best = current
    return best


def _pcm_samples(path: Path) -> tuple[array, int]:
    if not path.exists() or path.stat().st_size < 128:
        return array("h"), 16000
    with wave.open(str(path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        samples = array("h")
        samples.frombytes(frames)
        return samples, wav.getframerate() or 16000
