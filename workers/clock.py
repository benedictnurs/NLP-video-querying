from __future__ import annotations

import re

_CLOCK_RE = re.compile(
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?"
)


def format_clock(ms: int | float | None) -> str:
    total = max(int(ms or 0), 0) // 1000
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def parse_clock(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    match = _CLOCK_RE.search(text)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    millis = int((match.group(4) or "0").ljust(3, "0")[:3])
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def clip_bounds(record: dict) -> tuple[int, int]:
    start = int(record.get("start_ms") or 0)
    end = int(record.get("end_ms") or start)
    if end < start:
        end = start
    return start, end


def to_video_ms(record: dict, value) -> int | None:
    parsed = parse_clock(value)
    if parsed is None:
        return None
    start, end = clip_bounds(record)
    duration = max(end - start, 0)
    if start > 0 and 0 <= parsed <= duration + 1500:
        parsed = start + parsed
    return max(start, min(end, parsed))


def cell_entries(record: dict) -> list[dict]:
    cells = record.get("cells") or []
    start, end = clip_bounds(record)
    rows = []
    for cell in cells:
        at_s = float(cell.get("at_s") or 0)
        video_ms = int(cell.get("start_ms") or (start + at_s * 1000))
        rows.append(
            {
                "index": int(cell.get("index") or 0),
                "at_s": at_s,
                "start_ms": video_ms,
                "clock": cell.get("clock") or format_clock(video_ms),
            }
        )
    if rows:
        return rows
    count = int(record.get("splice_cells") or 0)
    duration_s = max((end - start) / 1000.0, 0.1)
    for index in range(count):
        at_s = (duration_s * (index + 0.5)) / max(count, 1)
        video_ms = start + int(at_s * 1000)
        rows.append(
            {
                "index": index,
                "at_s": round(at_s, 3),
                "start_ms": video_ms,
                "clock": format_clock(video_ms),
            }
        )
    return rows


def cell_start(record: dict, index) -> int | None:
    try:
        wanted = int(index)
    except (TypeError, ValueError):
        return None
    for cell in cell_entries(record):
        if cell["index"] == wanted:
            return cell["start_ms"]
    return None


def resolve_event_time(record: dict, item: dict) -> dict:
    start, end = clip_bounds(record)
    video_start = to_video_ms(record, item.get("start_ms"))
    if video_start is None:
        video_start = to_video_ms(record, item.get("start_clock") or item.get("clock"))
    if video_start is None:
        video_start = cell_start(record, item.get("cell"))
    if video_start is None:
        video_start = _hint_start(record, item)
    if video_start is None:
        video_start = start
    video_end = to_video_ms(record, item.get("end_ms"))
    if video_end is None:
        video_end = to_video_ms(record, item.get("end_clock"))
    if video_end is None:
        video_end = min(end, video_start + 30000)
    if video_end < video_start:
        video_end = min(end, video_start + 1000)
    cell = item.get("cell")
    if cell is None:
        cell = _nearest_cell(record, video_start)
    return {
        "start_ms": video_start,
        "end_ms": video_end,
        "clock": format_clock(video_start),
        "end_clock": format_clock(video_end),
        "seek_s": round(video_start / 1000.0, 3),
        "cell": cell,
    }


def transcript_timeline(record: dict, limit: int = 40) -> str:
    start, _end = clip_bounds(record)
    lines = []
    for item in (record.get("transcript_segments") or [])[:limit]:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        at = start + int(item.get("start_ms") or 0)
        lines.append(f"[{format_clock(at)}] {text}")
    if lines:
        return "\n".join(lines)
    text = (record.get("transcript") or "").strip()
    return f"[{format_clock(start)}] {text}" if text else "(no speech)"


def cell_timeline(record: dict) -> str:
    lines = []
    for cell in cell_entries(record):
        lines.append(f"cell_{cell['index']:02d} at {cell['clock']} ({cell['start_ms']}ms)")
    return "\n".join(lines) or "(no splice cells)"


def _hint_start(record: dict, item: dict) -> int | None:
    name = (item.get("type") or item.get("definition") or "").strip()
    for hit in record.get("keyword_hits") or []:
        if hit.get("definition") == name and hit.get("start_ms") is not None:
            return int(hit["start_ms"])
    signals = record.get("signals") or {}
    if name == "loud_impact" and signals.get("impact_at_ms") is not None:
        start, _end = clip_bounds(record)
        return start + int(signals["impact_at_ms"])
    return None


def _nearest_cell(record: dict, start_ms: int) -> int | None:
    best = None
    best_delta = None
    for cell in cell_entries(record):
        delta = abs(cell["start_ms"] - start_ms)
        if best_delta is None or delta < best_delta:
            best = cell["index"]
            best_delta = delta
    return best
