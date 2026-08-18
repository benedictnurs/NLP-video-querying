from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

from workers.clips import load_clip_records, save_clip_record
from workers.paths import video_work_dir

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def enrich_video_clips(video: dict) -> dict:
    from workers.events import attach_event_metadata, events_from_local
    from workers.summarize import enrich_clip_summaries

    enrich_clip_summaries(video)
    for record in load_clip_records(video["video_id"]):
        if record.get("events"):
            attach_event_metadata(record)
        else:
            attach_event_metadata(record, events_from_local(record))
            if record.get("event_types"):
                record["analysis_status"] = record.get("analysis_status") or "local_events"
        save_clip_record(video["video_id"], record)

    video = {**video, "status": "analyzed"}
    (video_work_dir(video["video_id"]) / "ingest.json").write_text(json.dumps(video, indent=2) + "\n")
    return video


def summarize_clip(record: dict, api_key: str, roster: list[dict] | None = None) -> dict:
    from workers.clock import cell_timeline, format_clock, transcript_timeline
    from workers.events import (
        attach_event_metadata,
        catalog_for_prompt,
        ensure_event_definitions,
        persist_new_definitions,
    )
    from workers.roster import public_roster
    from workers.summarize import local_tags, local_summary

    roster = roster if roster is not None else []
    from workers.local_tag import attach_local_descriptions

    attach_local_descriptions(record)
    images = _bucket_images(record)
    catalog = catalog_for_prompt()
    night_bit = (
        "This clip is night or very dark. If race or gender is not clearly visible, "
        "use unknown. Do not guess.\n"
        if record.get("night")
        else ""
    )
    proposed = [
        {
            "type": item.get("type") or item.get("definition"),
            "start_ms": item.get("start_ms"),
            "clock": item.get("clock") or item.get("start_clock"),
            "cell": item.get("cell"),
        }
        for item in (record.get("events") or [])
    ]
    prompt = (
        "You are summarizing a police bodycam/dashcam clip for a searchable graph. "
        "Return JSON only. Observable appearance and behavior only. No names, intent, "
        "guilt, intoxication, or legal conclusions. Event ids are scene labels, not charges.\n\n"
        "Event.type is the catalog id, e.g. traffic_stop. Reuse an existing id when the clip "
        "matches its context and evidence rules. If the scene matches an existing id but the "
        "catalog is missing a phrase, alias, or clearer description, put that in "
        "updated_definitions for that id. Only add a new_definitions id when nothing in the "
        "catalog fits, then use that same new id as the event type on this clip. New ids must "
        "be snake_case scene labels, not charges.\n\n"
        f"Event catalog:\n{catalog}\n\n"
        f"Time: {format_clock(record.get('start_ms'))}–{format_clock(record.get('end_ms'))} "
        f"({record.get('start_ms')}ms–{record.get('end_ms')}ms)\n"
        f"Clip: {record.get('id')}\n"
        f"Splice cells:\n{cell_timeline(record)}\n"
        f"Transcript (quote from this, times are video clocks):\n{transcript_timeline(record)}\n\n"
        f"Detector hints: {json.dumps(_public_entities(record))}\n"
        f"Known people already seen in this video: {json.dumps(public_roster(roster))}\n"
        f"Signals: {json.dumps(record.get('signals') or {})}\n"
        f"Proposed timed events from the quick scan (keep or correct clocks): {json.dumps(proposed)}\n"
        f"{night_bit}"
        "Look at the attached labeled splice (a grid of timestamped frames). "
        "If extra crops are attached, they are unlabeled person or plate closeups. "
        "Detector labels can be wrong; ignore boats, trains, or watercraft if the scene is a road, "
        "parking lot, or sidewalk.\n"
        "Create Person, Vehicle, and Event records for this clip.\n"
        "People: from the images, describe hair, glasses, clothes, shoes, bag, "
        "and any distinctive item (hat, mask, backpack). If that signature matches a "
        "known person, reuse that id and set match=existing. If not, match=new and "
        "id=person_N. Same clothes + hair or glasses means the same person. "
        "Set is_cop true only if uniform, badge, duty belt, or marked patrol ID is visible; "
        "false if they are clearly a civilian; unknown if you cannot tell. "
        "description must be a full sentence: who they appear to be, what they are wearing, "
        "and what they are doing. Do not invent attributes; use unknown when not visible.\n"
        "Vehicles: color, plate if characters are readable, and a one-line analysis of what "
        "the vehicle is doing. Same plate means the same vehicle.\n"
        "Clip summary: 4–8 sentences covering who is on screen (officer vs civilian, clothes), "
        "what they said (quote the transcript), what happened, and any other important "
        "observable facts in this window.\n"
        "Also list important facts that a later search would need: setting (road, sidewalk, "
        "car interior, residence), lighting, weather, number of people, weapons or objects "
        "in hand, restraints/handcuffs, medical aid or injury if visible, shouted commands, "
        "Miranda or rights language, license plates, vehicle motion (stopped, lights, "
        "pursuit), loud impacts, and anything unusual. Put those in important as short facts. "
        "Omit anything not actually seen or heard.\n"
        "Events: type (and definition) MUST be a catalog id, or a new_definitions id you just "
        "added. Each event MUST include start_ms and start_clock for when it began in the "
        "full video (not the clip start). Use splice cell clocks and transcript times. "
        "Each event summary must name the involved people by id "
        "and clothing, quote the relevant transcript lines, state the observable action, "
        "and include any other important details for that event. "
        "analysis is 3–6 sentences: who did what, what they were wearing, vehicles/plates, "
        "quoted speech, and other important observable context that supports this event id. "
        "Fill people_ids and vehicle_ids.\n"
        'Schema: {"summary": str, "important": [str], "tags": [str], '
        '"people": [{"id": str, "match": "existing"|"new", "is_cop": bool, "race": str, '
        '"gender": str, "hair": str, "glasses": str, "clothes": str, "shoes": str, '
        '"bag": str, "distinctive": str, "description": str}], '
        '"vehicles": [{"id": str, "color": str, "plate": str, "analysis": str}], '
        '"plates": [{"text": str, "vehicle_id": str, "confidence": float}], '
        '"events": [{"type": str, "definition": str, "start_ms": int, "end_ms": int, '
        '"start_clock": str, "cell": int, "summary": str, "analysis": str, '
        '"confidence": float, "people_ids": [str], "vehicle_ids": [str], '
        '"evidence": [{"modality": str, "value": str}]}], '
        '"new_definitions": [{"id": str, "title": str, "description": str, '
        '"how_to_confirm": str, "aliases": [str], "transcript_any": [str]}], '
        '"updated_definitions": [{"id": str, "title": str, "description": str, '
        '"how_to_confirm": str, "aliases": [str], "transcript_any": [str]}]}'
    )
    content = [{"type": "text", "text": prompt}]
    for image in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image}"},
            }
        )
    payload = {
        "model": os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite"),
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:8080",
            "X-Title": "video-intel",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.loads(response.read().decode())
    text = body["choices"][0]["message"]["content"]
    parsed = json.loads(text)
    _merge_vision(record, parsed, roster)
    local = local_tags(record)
    llm_tags = parsed.get("tags") or []
    merged = []
    for tag in [*local, *llm_tags]:
        if tag and tag not in merged:
            merged.append(tag)
    record["tags"] = merged
    record["summary"] = (parsed.get("summary") or "").strip() or local_summary(record)
    record["important"] = [
        str(item).strip()
        for item in (parsed.get("important") or [])
        if str(item).strip()
    ]
    record["summary_source"] = "openrouter_vision" if images else "openrouter"
    events = parsed.get("events") or []
    added = persist_new_definitions(
        parsed.get("new_definitions") or [],
        parsed.get("updated_definitions") or [],
    )
    added.extend(name for name in ensure_event_definitions(events) if name not in added)
    for name in added:
        if not any(
            item.get("type") == name or item.get("definition") == name or item.get("id") == name
            for item in events
        ):
            events.append(
                {
                    "type": name,
                    "definition": name,
                    "source": "gemini_learned",
                    "confidence": 0.55,
                    "context": "Newly proposed event type for this clip.",
                    "evidence": [{"modality": "note", "value": f"learned definition {name}"}],
                }
            )
    attach_event_metadata(record, events)
    record["model"] = {
        **(record.get("model") or {}),
        "summarizer": os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite"),
        "event_catalog": "definitions.yaml",
    }
    return record


def _bucket_images(record: dict) -> list[str]:
    from workers.fingerprint import is_key_clip

    encoded = []
    seen = set()
    uris: list[str] = []
    splice = record.get("tagged_splice_uri") or record.get("splice_uri")
    if splice:
        uris.append(splice)
    if is_key_clip(record):
        frames_dir = Path(record.get("folder_uri") or Path(record.get("clip_uri") or ".").parent) / "frames"
        cells = sorted(frames_dir.glob("cell_*.jpg")) if frames_dir.exists() else []
        if cells:
            uris.append(str(cells[len(cells) // 2]))
        crops = 0
        for entity in record.get("entities") or []:
            path = entity.get("crop_uri") if entity.get("type") == "person" else entity.get("plate_crop_uri")
            if path:
                uris.append(path)
                crops += 1
            if crops >= 2:
                break
    cap = 4 if is_key_clip(record) else 1
    for uri in uris:
        path = Path(uri)
        key = str(path)
        if key in seen or not path.exists() or path.stat().st_size < 32:
            continue
        seen.add(key)
        encoded.append(base64.b64encode(path.read_bytes()).decode())
        if len(encoded) >= cap:
            break
    return encoded


def _public_entities(record: dict) -> list[dict]:
    public = []
    for item in record.get("entities") or []:
        kind = item.get("type")
        if kind == "person":
            public.append(
                {
                    "id": item.get("id"),
                    "type": "person",
                    "label": item.get("label") or "person",
                }
            )
        elif kind == "vehicle":
            public.append(
                {
                    "id": item.get("id"),
                    "type": "vehicle",
                    "label": item.get("label"),
                    "color": item.get("color"),
                    "plate": item.get("plate"),
                }
            )
        else:
            public.append(
                {
                    "id": item.get("id"),
                    "type": kind or "object",
                    "label": item.get("label"),
                }
            )
    return public


def _merge_vision(record: dict, parsed: dict, roster: list[dict] | None = None) -> None:
    from workers.roster import upsert_person

    roster = roster if roster is not None else []
    clip_id = record.get("id") or ""
    people = []
    for item in parsed.get("people") or []:
        card = upsert_person(roster, item, clip_id)
        people.append(
            {
                "id": card["id"],
                "type": "person",
                "race": card.get("race") or "unknown",
                "gender": card.get("gender") or "unknown",
                "hair": card.get("hair") or "unknown",
                "glasses": card.get("glasses") or "unknown",
                "clothes": card.get("clothes") or "",
                "shoes": card.get("shoes") or "unknown",
                "bag": card.get("bag") or "unknown",
                "distinctive": card.get("distinctive") or "",
                "signature": card.get("signature") or "",
                "description": card.get("description") or "",
                "is_cop": card.get("is_cop"),
                "source": "gemini",
            }
        )
    vehicles_by_id = {item.get("id"): item for item in parsed.get("vehicles") or [] if item.get("id")}
    kept = []
    for entity in record.get("entities") or []:
        if entity.get("type") == "person":
            continue
        if entity.get("type") == "vehicle":
            update = vehicles_by_id.pop(entity.get("id"), {}) or {}
            color = (update.get("color") or entity.get("color") or "").strip().lower()
            plate = _normalize_plate(update.get("plate") or entity.get("plate"))
            kept.append(
                {
                    "id": entity.get("id"),
                    "type": "vehicle",
                    "color": color,
                    "plate": plate,
                    "analysis": (update.get("analysis") or "").strip(),
                    "source": "gemini" if update else entity.get("source"),
                }
            )
            continue
        kept.append(entity)
    for update in vehicles_by_id.values():
        plate = _normalize_plate(update.get("plate"))
        kept.append(
            {
                "id": update.get("id") or f"vehicle_{len(kept)}",
                "type": "vehicle",
                "color": (update.get("color") or "").strip().lower(),
                "plate": plate,
                "analysis": (update.get("analysis") or "").strip(),
                "source": "gemini",
            }
        )
    if people:
        record["entities"] = [*people, *kept]
    else:
        record["entities"] = kept
    _merge_plates(record, parsed)
    from workers.local_tag import attach_local_descriptions

    attach_local_descriptions(record)


def _merge_plates(record: dict, parsed: dict) -> None:
    plates = []
    seen = set()
    for item in parsed.get("plates") or []:
        text = _normalize_plate(item.get("text"))
        if not text or text in seen:
            continue
        seen.add(text)
        plates.append(
            {
                "text": text,
                "vehicle_id": item.get("vehicle_id"),
                "confidence": item.get("confidence") or 0.6,
                "source": "gemini",
            }
        )
    for entity in record.get("entities") or []:
        plate = _normalize_plate(entity.get("plate"))
        if entity.get("type") != "vehicle" or not plate or plate in seen:
            continue
        seen.add(plate)
        plates.append(
            {
                "text": plate,
                "vehicle_id": entity.get("id"),
                "confidence": 0.6,
                "source": "gemini",
            }
        )
        entity["plate"] = plate
    record["plates"] = plates
    for plate in plates:
        vehicle_id = plate.get("vehicle_id")
        if not vehicle_id:
            continue
        for entity in record.get("entities") or []:
            if entity.get("id") == vehicle_id:
                entity["plate"] = plate["text"]
                entity["needs_plate_read"] = False
                break


def _normalize_plate(value) -> str:
    text = "".join(ch for ch in str(value or "").upper() if ch.isalnum())
    if len(text) < 4 or len(text) > 10:
        return ""
    if not any(ch.isdigit() for ch in text):
        return ""
    return text
