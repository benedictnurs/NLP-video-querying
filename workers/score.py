from __future__ import annotations

from pathlib import Path

from workers.audio_signals import audio_signals
from workers.clips import load_clip_records, save_clip_record, save_video_status
from workers.events import attach_event_metadata, events_from_local, load_definitions
from workers.fingerprint import denoise_clip, is_key_clip, scene_prior


def score_video_clips(video: dict) -> dict:
    definitions = load_definitions()
    records = load_clip_records(video["video_id"])
    scene = scene_prior(records)
    for record in records:
        prev = record.get("signals") or {}
        wav = Path(record.get("audio_uri") or "")
        if wav.exists():
            fresh = audio_signals(wav)
            record["signals"] = {
                **prev,
                **fresh,
                "yelling": fresh["loud_speech"],
                "repeated_commands": prev.get("repeated_commands") or 0,
            }
        denoise_clip(record, scene)
        candidates = _candidates(record, definitions)
        record["candidate_definitions"] = candidates
        record["key_clip"] = is_key_clip(record)
        record["analysis_status"] = "needed" if record["key_clip"] or record.get("person_count") or record.get("vehicle_count") else "local_only"
        if not isinstance(record.get("important"), list):
            record.pop("important", None)
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
