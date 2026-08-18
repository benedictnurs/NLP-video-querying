from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from workers.media import ffprobe
from workers.paths import (
    STABILITY_SECONDS,
    VIDEO_EXTENSIONS,
    data_dir,
    registry_path,
    video_drop_dir,
    video_work_dir,
)

_STEM_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_CHUNK = 1024 * 1024


def list_new_videos() -> list[str]:
    drop = video_drop_dir()
    drop.mkdir(parents=True, exist_ok=True)
    now = time.time()
    pending: list[str] = []

    for path in sorted(drop.iterdir()):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if path.name.startswith("."):
            continue
        stat = path.stat()
        if now - stat.st_mtime < STABILITY_SECONDS:
            continue
        if cached_video(path):
            continue
        pending.append(str(path))

    return pending


def next_new_video() -> str | None:
    pending = list_new_videos()
    return pending[0] if pending else None


def run_ingest(source_path: str) -> dict:
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"Video not found: {source}")

    cached = cached_video(source)
    if cached and cached.get("status") == "graphed":
        return {**cached, "cached": True}

    stat = source.stat()
    video_id = resolve_video_id(source)
    work_dir = video_work_dir(video_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    existing_path = work_dir / "ingest.json"
    if existing_path.exists():
        existing = json.loads(existing_path.read_text())
        if existing.get("status") == "graphed" and _same_file(existing, stat):
            save_registry_entry(
                source.name,
                existing.get("video_id") or video_id,
                content_fingerprint(source, stat.st_size),
                "graphed",
                stat.st_size,
            )
            return {**existing, "cached": True}

    original = work_dir / f"original{source.suffix.lower()}"
    if not original.exists() or original.stat().st_size != stat.st_size:
        shutil.copy2(source, original)

    probe = ffprobe(original)
    record = {
        "video_id": video_id,
        "source_name": source.name,
        "source_path": str(source),
        "original_uri": str(original),
        "audio_uri": str(work_dir / "audio_16k.wav"),
        "processing_uri": str(work_dir / "processing.mp4"),
        "duration_s": probe.get("duration_s"),
        "fps": probe.get("fps"),
        "width": probe.get("width"),
        "height": probe.get("height"),
        "size_bytes": stat.st_size,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "status": "copied",
        "cached": False,
    }
    existing_path.write_text(json.dumps(record, indent=2) + "\n")
    save_registry_entry(
        source.name,
        video_id,
        content_fingerprint(source, stat.st_size),
        "ingested",
        stat.st_size,
    )
    return record


def cached_video(source: Path) -> dict | None:
    if not source.is_file():
        return None
    stat = source.stat()
    fingerprint = content_fingerprint(source, stat.st_size)
    registry = load_registry()
    for name, entry in registry.items():
        if name.startswith("_") or not isinstance(entry, dict):
            continue
        graphed = entry.get("status") == "graphed"
        same_fp = entry.get("fingerprint") == fingerprint
        same_name_size = name == source.name and int(entry.get("size_bytes") or 0) == stat.st_size
        if graphed and (same_fp or same_name_size):
            record = _load_ingest(entry.get("video_id") or make_video_id(source))
            if record:
                return record
    video_id = resolve_video_id(source)
    record = _load_ingest(video_id)
    if record and record.get("status") == "graphed" and _same_file(record, stat):
        return record
    return None


def resolve_video_id(source: Path) -> str:
    stem = make_video_id(source)
    stem_record = _load_ingest(stem)
    if stem_record:
        return stem
    videos = data_dir() / "videos"
    if not videos.exists():
        return stem
    for child in sorted(videos.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name != stem and not child.name.startswith(f"{stem}_"):
            continue
        record = _load_ingest(child.name)
        if not record:
            continue
        if record.get("source_name") == source.name or child.name.startswith(f"{stem}_"):
            if record.get("status") == "graphed":
                return child.name
    return stem


def make_video_id(path: Path) -> str:
    stem = _STEM_RE.sub("_", path.stem).strip("_").lower() or "video"
    return stem


def content_fingerprint(path: Path, size: int) -> str:
    digest = hashlib.sha256()
    digest.update(str(size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(_CHUNK))
        if size > _CHUNK * 2:
            handle.seek(size - _CHUNK)
            digest.update(handle.read(_CHUNK))
    return digest.hexdigest()


def load_registry() -> dict:
    path = registry_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_registry_entry(
    source_name: str,
    video_id: str,
    fingerprint: str,
    status: str,
    size_bytes: int | None = None,
) -> None:
    registry = load_registry()
    previous = registry.get(source_name) or {}
    registry[source_name] = {
        "video_id": video_id,
        "fingerprint": fingerprint,
        "status": status,
        "size_bytes": size_bytes if size_bytes is not None else previous.get("size_bytes"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2) + "\n")


def _load_ingest(video_id: str) -> dict | None:
    if not video_id:
        return None
    path = video_work_dir(video_id) / "ingest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _same_file(record: dict, stat) -> bool:
    size = int(record.get("size_bytes") or 0)
    return size == 0 or size == stat.st_size
