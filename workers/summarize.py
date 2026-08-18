from __future__ import annotations

import os

from workers.clips import load_clip_records, save_clip_record, save_video_status


def tag_and_summarize_local(record: dict) -> dict:
    tags = local_tags(record)
    record["tags"] = tags
    record["summary"] = local_summary(record)
    record["summary_source"] = "local"
    return record


def local_tags(record: dict) -> list[str]:
    tags: list[str] = []
    transcript = (record.get("transcript") or "").strip()
    tags.append("speech" if transcript else "no_speech")
    if record.get("person_count"):
        tags.append("person")
    if record.get("vehicle_count"):
        tags.append("vehicle")
    if record.get("object_count"):
        tags.append("object")
    if record.get("needs_vision"):
        tags.append("needs_vision")
    for entity in record.get("entities") or []:
        label = entity.get("label")
        if label and label not in tags:
            tags.append(label)
        color = entity.get("upper_clothing_color")
        if entity.get("type") == "person" and color and color != "unknown":
            color_tag = f"clothing_{color}"
            if color_tag not in tags:
                tags.append(color_tag)
        if entity.get("uniform_like") and "uniform_like" not in tags:
            tags.append("uniform_like")
    signals = record.get("signals") or {}
    if (signals.get("loud_impact") or 0) >= 0.5:
        tags.append("loud_impact")
    if (signals.get("yelling") or 0) >= 0.5:
        tags.append("yelling")
    if (signals.get("repeated_commands") or 0) >= 2:
        tags.append("repeated_commands")
    for hit in record.get("keyword_hits") or []:
        name = hit.get("definition")
        if name and name not in tags:
            tags.append(name)
    for name in record.get("event_types") or []:
        if name not in tags:
            tags.append(name)
    return tags


def local_summary(record: dict) -> str:
    start = _fmt_ms(record.get("start_ms") or 0)
    end = _fmt_ms(record.get("end_ms") or 0)
    people = record.get("person_count") or 0
    vehicles = record.get("vehicle_count") or 0
    objects = record.get("object_count") or 0
    descriptions = record.get("people_descriptions") or []
    labels = sorted(
        {
            item.get("label")
            for item in (record.get("entities") or [])
            if item.get("label")
        }
    )
    visible = f"{people} person(s), {vehicles} vehicle(s), {objects} object(s)"
    if labels:
        visible += f" ({', '.join(labels)})"
    clothing = " ".join(descriptions[:4])
    transcript = (record.get("transcript") or "").strip()
    if transcript:
        snippet = transcript[:400]
        if len(transcript) > 400:
            snippet += "…"
        speech = f"Speech: {snippet}"
    else:
        speech = "No speech transcribed."
    tags = record.get("tags") or []
    tag_bit = f" Tags: {', '.join(tags)}." if tags else ""
    clothing_bit = f" People: {clothing}." if clothing else ""
    return f"Clip {record.get('id')} ({start}–{end}). Visible: {visible}.{clothing_bit} {speech}{tag_bit}"


def enrich_clip_summaries(video: dict) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    records = sorted(
        load_clip_records(video["video_id"]),
        key=lambda item: int(item.get("index") or 0),
    )
    if not api_key:
        for record in records:
            if not record.get("summary"):
                tag_and_summarize_local(record)
            save_clip_record(video["video_id"], record)
        return save_video_status(video, "summarized")

    from workers.openrouter import summarize_clip
    from workers.roster import load_roster, save_roster

    roster = load_roster(video["video_id"])
    from workers.fingerprint import should_ask_gemini

    for record in records:
        if not should_ask_gemini(record):
            if not record.get("summary"):
                tag_and_summarize_local(record)
            record["summary_source"] = record.get("summary_source") or "local"
            save_clip_record(video["video_id"], record)
            continue
        try:
            updated = summarize_clip(record, api_key, roster)
        except Exception as exc:
            updated = tag_and_summarize_local(record)
            updated["summary_error"] = str(exc)[:500]
        save_clip_record(video["video_id"], updated)
    save_roster(video["video_id"], roster)
    return save_video_status(video, "summarized")


def _fmt_ms(ms: int) -> str:
    total = max(int(ms) // 1000, 0)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
