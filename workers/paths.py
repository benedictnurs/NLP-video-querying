from __future__ import annotations

import os
import re
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}
STABILITY_SECONDS = 15
AIRFLOW_DATA = Path("/opt/airflow/data")
AIRFLOW_ROOT = Path("/opt/airflow")
_HASHED_VIDEO = re.compile(r"(/videos/)([^/]+)_([0-9a-f]{8})(/)")
URI_KEYS = (
    "clip_uri",
    "splice_uri",
    "tagged_splice_uri",
    "audio_uri",
    "original_uri",
    "processing_uri",
    "source_path",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def video_drop_dir() -> Path:
    return Path(os.environ.get("VIDEO_DROP_DIR", "/videos"))


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/opt/airflow/data"))


def host_data_dir() -> Path:
    env = os.environ.get("HOST_DATA_DIR") or os.environ.get("DATA_DIR")
    if env:
        return Path(env)
    return repo_root() / "data"


def registry_path() -> Path:
    return data_dir() / "registry.json"


def video_work_dir(video_id: str) -> Path:
    return data_dir() / "videos" / video_id


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def to_local_path(uri: str | None) -> Path | None:
    """Map an Airflow or graph URI onto the host data folder."""
    if not uri:
        return None
    raw = str(uri).strip()
    if raw.startswith("file://"):
        raw = raw[7:]
    path = Path(raw)
    data = host_data_dir()
    drop = video_drop_dir()
    candidates: list[Path] = []
    for prefix, root in (
        (AIRFLOW_DATA, data),
        (AIRFLOW_ROOT, repo_root()),
        (Path("/videos"), Path(os.environ.get("HOST_VIDEO_DIR") or str(repo_root() / "videos"))),
    ):
        try:
            candidates.append(root / path.relative_to(prefix))
        except ValueError:
            continue
    if str(drop) not in ("/videos",) and str(path).startswith("/videos/"):
        candidates.append(Path(str(path).replace("/videos", str(drop), 1)))
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.append(data / path)
        candidates.append(repo_root() / path)
    expanded: list[Path] = []
    for candidate in candidates:
        expanded.append(candidate)
        unhashed = Path(_HASHED_VIDEO.sub(r"\1\2\4", str(candidate)))
        if unhashed != candidate:
            expanded.append(unhashed)
    seen: set[str] = set()
    for candidate in expanded:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate.resolve()
    return expanded[0].resolve() if expanded else None


def persist_media_uri(uri: str | None) -> str | None:
    """URI stored on the graph: host-local path when HOST_DATA_DIR is set, else a path that exists here."""
    if not uri:
        return uri
    raw = str(uri).strip()
    if raw.startswith("file://"):
        raw = raw[7:]
    path = Path(raw)
    host = os.environ.get("HOST_DATA_DIR")
    if host:
        try:
            mapped = Path(host) / path.relative_to(AIRFLOW_DATA)
            mapped = Path(_HASHED_VIDEO.sub(r"\1\2\4", str(mapped)))
            return str(mapped)
        except ValueError:
            pass
        video_host = os.environ.get("HOST_VIDEO_DIR")
        if video_host and (str(path) == "/videos" or str(path).startswith("/videos/")):
            return str(Path(video_host) / path.relative_to("/videos"))
    local = to_local_path(raw)
    if not local:
        return raw
    unhashed = Path(_HASHED_VIDEO.sub(r"\1\2\4", str(local)))
    if unhashed.exists():
        return str(unhashed.resolve())
    return str(local)
