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
    registry_path,
    video_drop_dir,
    video_work_dir,
)


def list_new_videos() -> list[str]:
    drop = video_drop_dir()
    drop.mkdir(parents=True, exist_ok=True)
    registry = load_registry()
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
        fingerprint = fingerprint_for(path, stat.st_size, stat.st_mtime)
        existing = registry.get(path.name)
        if existing and existing.get("fingerprint") == fingerprint and existing.get("status") == "graphed":
            continue
        pending.append(str(path))

    return pending


def run_ingest(source_path: str) -> dict:
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"Video not found: {source}")

    stat = source.stat()
    video_id = make_video_id(source, stat.st_size)
    work_dir = video_work_dir(video_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    original = work_dir / f"original{source.suffix.lower()}"
    if not original.exists() or original.stat().st_size != stat.st_size:
        shutil.copy2(source, original)

    probe = ffprobe(original)
    audio_uri = work_dir / "audio_16k.wav"
    processing_uri = work_dir / "processing.mp4"

    record = {
        "video_id": video_id,
        "source_name": source.name,
        "source_path": str(source),
        "original_uri": str(original),
        "audio_uri": str(audio_uri),
        "processing_uri": str(processing_uri),
        "duration_s": probe.get("duration_s"),
        "fps": probe.get("fps"),
        "width": probe.get("width"),
        "height": probe.get("height"),
        "size_bytes": stat.st_size,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "status": "copied",
    }
    (work_dir / "ingest.json").write_text(json.dumps(record, indent=2) + "\n")
    save_registry_entry(source.name, video_id, fingerprint_for(source, stat.st_size, stat.st_mtime), "ingested")
    return record


def make_video_id(path: Path, size: int) -> str:
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", path.stem).strip("_").lower() or "video"
    digest = hashlib.sha256(f"{path.name}:{size}".encode()).hexdigest()[:8]
    return f"{stem}_{digest}"


def fingerprint_for(path: Path, size: int, mtime: float) -> str:
    return hashlib.sha256(f"{path.name}:{size}:{int(mtime)}".encode()).hexdigest()


def load_registry() -> dict:
    path = registry_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_registry_entry(source_name: str, video_id: str, fingerprint: str, status: str) -> None:
    registry = load_registry()
    registry[source_name] = {
        "video_id": video_id,
        "fingerprint": fingerprint,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2) + "\n")
