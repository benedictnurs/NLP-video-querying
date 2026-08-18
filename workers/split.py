from __future__ import annotations

import json
import os
from pathlib import Path

from workers.media import cut_audio, cut_processing_clip
from workers.clips import clip_folder
from workers.paths import video_work_dir

CLIP_SECONDS = float(os.environ.get("CLIP_SECONDS", "180"))
OVERLAP_SECONDS = float(os.environ.get("CLIP_OVERLAP_SECONDS", "10"))


def split_video(video: dict) -> dict:
    duration_s = float(video.get("duration_s") or 0)
    if duration_s <= 0:
        raise ValueError(f"No duration for {video['video_id']}")

    work_dir = video_work_dir(video["video_id"])
    clips_dir = work_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    clips = [cut_window(video, index, start_s, end_s) for index, (start_s, end_s) in enumerate(clip_windows(duration_s))]
    video = {
        **video,
        "clips_dir": str(clips_dir),
        "clip_count": len(clips),
        "status": "split",
    }
    (work_dir / "ingest.json").write_text(json.dumps(video, indent=2) + "\n")
    return video


def cut_window(video: dict, index: int, start_s: float, end_s: float) -> dict:
    clip_id = f"clip_{index:04d}"
    folder = clip_folder(video["video_id"], clip_id)
    clip_mp4 = folder / "video.mp4"
    clip_wav = folder / "audio.wav"
    duration = max(end_s - start_s, 0.05)
    source, already_processed = _clip_video_source(video)
    if not clip_mp4.exists() or clip_mp4.stat().st_size < 128:
        cut_processing_clip(source, clip_mp4, start_s, duration, already_processed=already_processed)
    audio_source = _clip_audio_source(video)
    if not clip_wav.exists() or clip_wav.stat().st_size < 128:
        cut_audio(audio_source, clip_wav, start_s, duration)
    return {
        "id": clip_id,
        "video_id": video["video_id"],
        "index": index,
        "start_ms": int(round(start_s * 1000)),
        "end_ms": int(round(end_s * 1000)),
        "clip_uri": str(clip_mp4),
        "audio_uri": str(clip_wav),
        "folder_uri": str(folder),
        "transcript": "",
    }


def _clip_video_source(video: dict) -> tuple[Path, bool]:
    processing = Path(video["processing_uri"])
    if processing.exists() and processing.stat().st_size > 128:
        return processing, True
    return Path(video["original_uri"]), False


def _clip_audio_source(video: dict) -> Path:
    audio = Path(video["audio_uri"])
    if audio.exists() and audio.stat().st_size > 128:
        return audio
    return Path(video["original_uri"])


def clip_windows(duration_s: float, clip_seconds: float | None = None, overlap_seconds: float | None = None) -> list[tuple[float, float]]:
    window = CLIP_SECONDS if clip_seconds is None else clip_seconds
    overlap = OVERLAP_SECONDS if overlap_seconds is None else overlap_seconds
    if duration_s <= window:
        return [(0.0, duration_s)]
    step = max(window - overlap, 1.0)
    starts = []
    start = 0.0
    while start < duration_s:
        end = min(start + window, duration_s)
        starts.append((start, end))
        if end >= duration_s:
            break
        start += step
    return starts
