from __future__ import annotations

import os
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}
STABILITY_SECONDS = 15


def video_drop_dir() -> Path:
    return Path(os.environ.get("VIDEO_DROP_DIR", "/videos"))


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/opt/airflow/data"))


def registry_path() -> Path:
    return data_dir() / "registry.json"


def video_work_dir(video_id: str) -> Path:
    return data_dir() / "videos" / video_id


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path
