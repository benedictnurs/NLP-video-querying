from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from workers.paths import (
    URI_KEYS,
    host_data_dir,
    persist_media_uri,
    repo_root,
    to_local_path,
)

ROOT = repo_root()
_URI_PREFIXES = ("/opt/airflow", "file:///opt/airflow")


def localize_uri(uri: str | None) -> str | None:
    return persist_media_uri(uri)


def localize_payload(value):
    if isinstance(value, str) and (
        value.startswith(_URI_PREFIXES) or value.startswith("/videos/")
    ):
        return localize_uri(value)
    if isinstance(value, list):
        return [localize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [localize_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: localize_payload(item) for key, item in value.items()}
    return value


def allowed_path(path: Path) -> bool:
    resolved = path.resolve()
    roots = [
        host_data_dir().resolve(),
        (ROOT / "data").resolve(),
        (ROOT / "videos").resolve(),
    ]
    return any(resolved == root or root in resolved.parents for root in roots)


def reveal_in_finder(path: Path, open_file: bool = False) -> None:
    target = str(path)
    if sys.platform == "darwin":
        if open_file or path.is_dir():
            subprocess.run(["open", target], check=True)
        else:
            subprocess.run(["open", "-R", target], check=True)
        return
    if sys.platform == "win32":
        subprocess.run(["explorer", "/select,", target], check=False)
        return
    subprocess.run(
        ["xdg-open", str(path if open_file or path.is_dir() else path.parent)],
        check=False,
    )


def clip_image_paths(row: dict, include_frames: bool = False, max_frames: int = 6) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for key in ("tagged_splice_uri", "splice_uri"):
        local = to_local_path(row.get(key))
        if local and local.exists() and local.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            if str(local) not in seen:
                seen.add(str(local))
                paths.append(local)
            break
    clip_local = to_local_path(row.get("clip_uri"))
    folder = clip_local.parent if clip_local else None
    if include_frames and folder:
        frame_dir = folder / "frames"
        if frame_dir.is_dir():
            cells = sorted(frame_dir.glob("cell_*.jpg"))[: max(0, max_frames)]
            for cell in cells:
                resolved = cell.resolve()
                if str(resolved) not in seen:
                    seen.add(str(resolved))
                    paths.append(resolved)
    return paths


def rewrite_graph_uris() -> dict:
    from graph_mcp.db import driver

    updated = 0
    skipped = 0
    samples = []
    with driver().session() as session:
        rows = session.run(
            """
            MATCH (n)
            WHERE any(key IN keys(n) WHERE key ENDS WITH '_uri' OR key = 'source_path')
            RETURN elementId(n) AS id, labels(n) AS labels, n AS node
            """
        )
        for row in rows:
            node = row["node"]
            sets = {}
            for key in URI_KEYS:
                if key not in node:
                    continue
                old = node.get(key)
                new = persist_media_uri(old)
                if new and new != old:
                    sets[key] = new
            if not sets:
                skipped += 1
                continue
            assignments = ", ".join(f"n.{key} = ${key}" for key in sets)
            session.run(
                f"MATCH (n) WHERE elementId(n) = $id SET {assignments}",
                id=row["id"],
                **sets,
            )
            updated += 1
            if len(samples) < 6:
                samples.append(
                    {
                        "labels": list(row["labels"]),
                        "changed": sets,
                    }
                )
    return {"updated_nodes": updated, "unchanged": skipped, "data_dir": str(host_data_dir()), "samples": samples}
