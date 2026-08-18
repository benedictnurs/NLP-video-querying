from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from workers.clock import format_clock
from workers.media import extract_jpeg
from workers.night import is_night

CELL_WIDTH = 320
CELL_HEIGHT = 180
SPLICE_COLUMNS = 4


def build_frame_splice(
    clip_path: Path,
    dest: Path,
    duration_s: float,
    frame_count: int,
    clip_start_ms: int = 0,
) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cells = []
    tiles: list[Image.Image] = []
    for index in range(max(1, frame_count)):
        at_s = (duration_s * (index + 0.5)) / max(frame_count, 1)
        cell_dir = dest.parent / "frames"
        cell_dir.mkdir(parents=True, exist_ok=True)
        cell_path = cell_dir / f"cell_{index:02d}.jpg"
        try:
            extract_jpeg(clip_path, cell_path, at_s)
        except RuntimeError:
            continue
        if not cell_path.exists() or cell_path.stat().st_size < 32:
            continue
        try:
            frame = Image.open(cell_path).convert("RGB")
        except OSError:
            continue
        night, luma = is_night(frame)
        video_ms = int(clip_start_ms + at_s * 1000)
        tiles.append(_fit_cell(frame, CELL_WIDTH, CELL_HEIGHT))
        cells.append(
            {
                "index": index,
                "at_s": round(at_s, 3),
                "start_ms": video_ms,
                "clock": format_clock(video_ms),
                "uri": str(cell_path),
                "night": night,
                "luma": round(luma, 1),
            }
        )

    if not tiles:
        return {"splice_uri": None, "cells": []}

    columns = min(SPLICE_COLUMNS, len(tiles))
    rows = math.ceil(len(tiles) / columns)
    canvas = Image.new("RGB", (columns * CELL_WIDTH, rows * CELL_HEIGHT), (18, 18, 18))
    for index, tile in enumerate(tiles):
        col = index % columns
        row = index // columns
        x = col * CELL_WIDTH
        y = row * CELL_HEIGHT
        canvas.paste(tile, (x, y))
        cells[index]["x"] = x
        cells[index]["y"] = y
        cells[index]["width"] = CELL_WIDTH
        cells[index]["height"] = CELL_HEIGHT
    canvas.save(dest, quality=95)
    return {
        "splice_uri": str(dest),
        "cells": cells,
        "columns": columns,
        "rows": rows,
        "cell_count": len(cells),
    }


def compose_splice(cells: list[dict], dest: Path, uri_key: str = "uri") -> str | None:
    tiles: list[Image.Image] = []
    for cell in cells:
        path = Path(cell.get(uri_key) or cell.get("uri") or "")
        if not path.exists():
            continue
        try:
            frame = Image.open(path).convert("RGB")
        except OSError:
            continue
        tiles.append(_fit_cell(frame, CELL_WIDTH, CELL_HEIGHT))
    if not tiles:
        return None
    columns = min(SPLICE_COLUMNS, len(tiles))
    rows = math.ceil(len(tiles) / columns)
    canvas = Image.new("RGB", (columns * CELL_WIDTH, rows * CELL_HEIGHT), (18, 18, 18))
    for index, tile in enumerate(tiles):
        col = index % columns
        row = index // columns
        canvas.paste(tile, (col * CELL_WIDTH, row * CELL_HEIGHT))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, quality=95)
    return str(dest)


def _fit_cell(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = min(width / image.width, height / image.height)
    new_w = max(1, int(image.width * scale))
    new_h = max(1, int(image.height * scale))
    resized = image.resize((new_w, new_h), Image.Resampling.BILINEAR)
    cell = Image.new("RGB", (width, height), (18, 18, 18))
    cell.paste(resized, ((width - new_w) // 2, (height - new_h) // 2))
    return cell
