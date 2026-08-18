from __future__ import annotations

import json
from pathlib import Path

from workers.paths import video_work_dir


def clip_folder(video_id: str, clip_id: str) -> Path:
    path = video_work_dir(video_id) / "clips" / clip_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def clip_json_path(video_id: str, clip_id: str) -> Path:
    return video_work_dir(video_id) / "clips" / clip_id / "clip.json"


def clip_analysis_done(video_id: str, clip_id: str) -> bool:
    folder = video_work_dir(video_id) / "clips" / clip_id
    path = folder / "clip.json"
    video = folder / "video.mp4"
    if not path.exists() or not video.exists() or video.stat().st_size < 128:
        return False
    try:
        data = json.loads(path.read_text())
    except Exception:
        return False
    return "index" in data and "model" in data


def load_clip_records(video_id: str) -> list[dict]:
    clips_dir = video_work_dir(video_id) / "clips"
    files = sorted(clips_dir.glob("clip_*/clip.json"))
    if not files:
        files = sorted(clips_dir.glob("clip_*.json"))
    records = [json.loads(path.read_text()) for path in files]
    records.sort(key=lambda item: item["index"])
    return records


def save_clip_record(video_id: str, record: dict) -> None:
    folder = clip_folder(video_id, record["id"])
    transcript = record.get("transcript") or ""
    tmp = folder / "clip.json.tmp"
    tmp.write_text(json.dumps(_clip_json(record), indent=2) + "\n")
    tmp.replace(folder / "clip.json")
    (folder / "transcript.txt").write_text(transcript + ("\n" if transcript else ""))


def save_video_transcripts(video_id: str) -> Path:
    lines = []
    for record in load_clip_records(video_id):
        start = _ms_clock(record.get("start_ms") or 0)
        end = _ms_clock(record.get("end_ms") or 0)
        text = (record.get("transcript") or "").strip() or "(no speech)"
        lines.append(f"## {record.get('id')}  {start}–{end}\n\n{text}\n")
    path = video_work_dir(video_id) / "transcripts.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n")
    return path


def _clip_json(record: dict) -> dict:
    first = [
        "id",
        "video_id",
        "index",
        "start_ms",
        "end_ms",
        "transcript",
        "transcript_segments",
        "asr_error",
        "summary",
        "important",
        "clip_uri",
        "audio_uri",
        "folder_uri",
        "splice_uri",
        "tagged_splice_uri",
    ]
    ordered = {key: record[key] for key in first if key in record}
    for key, value in record.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def _ms_clock(ms: int) -> str:
    total = max(int(ms), 0) // 1000
    return f"{total // 60:02d}:{total % 60:02d}"


def save_video_status(video: dict, status: str) -> dict:
    updated = {**video, "status": status}
    path = video_work_dir(video["video_id"]) / "ingest.json"
    path.write_text(json.dumps(updated, indent=2) + "\n")
    return updated
