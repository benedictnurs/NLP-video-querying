from __future__ import annotations

from workers.clips import load_clip_records, save_clip_record, save_video_status
from workers.events import attach_event_metadata, events_from_local, load_definitions


def score_video_clips(video: dict) -> dict:
    definitions = load_definitions()
    for record in load_clip_records(video["video_id"]):
        candidates = _candidates(record, definitions)
        record["important"] = bool(candidates)
        record["candidate_definitions"] = candidates
        record["analysis_status"] = "needed" if candidates else "local_only"
        attach_event_metadata(record, events_from_local(record, definitions))
        save_clip_record(video["video_id"], record)
    return save_video_status(video, "scored")


def _candidates(record: dict, definitions: dict) -> list[str]:
    signals = record.get("signals") or {}
    hits = {item["definition"] for item in record.get("keyword_hits") or []}
    names = []
    for name, spec in definitions.items():
        if name in hits:
            names.append(name)
            continue
        required = spec.get("any") or []
        if not required:
            continue
        matched = [key for key in required if _truthy(signals.get(key))]
        minimum = spec.get("require_min_signals") or 1
        excluded = spec.get("exclude") or []
        if any(_truthy(signals.get(key)) for key in excluded):
            continue
        if len(matched) >= minimum:
            names.append(name)
    return names


def _truthy(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value >= 0.5
    return bool(value)
