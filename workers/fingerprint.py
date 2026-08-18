from __future__ import annotations

import os
from collections import Counter

from workers.clips import load_clip_records, save_clip_record, save_video_status
from workers.local_tag import attach_local_descriptions
from workers.roster import (
    make_signature,
    save_identities,
    save_roster,
    upsert_object,
    upsert_person,
    upsert_plate,
    upsert_vehicle,
)

KEY_EVENT_TYPES = {
    "arrest",
    "handcuffing",
    "physical_restraint",
    "loud_impact",
    "foot_pursuit",
    "vehicle_pursuit",
    "search_person",
    "search_vehicle",
    "miranda_warning",
    "verbal_escalation",
}
LAND_LABELS = {"car", "truck", "bus", "motorcycle", "bicycle", "traffic light", "stop sign"}
WATER_LABELS = {"boat", "surfboard"}
RARE_ON_ROAD = {"boat", "train", "airplane", "bird", "bench", "skis", "snowboard"}


def fingerprint_video(video: dict) -> dict:
    records = sorted(
        load_clip_records(video["video_id"]),
        key=lambda item: int(item.get("index") or 0),
    )
    scene = scene_prior(records)
    for record in records:
        denoise_clip(record, scene)
        record["key_clip"] = is_key_clip(record)
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    identities = None
    if api_key:
        try:
            from workers.fingerprint_agent import run_fingerprint_agent

            identities = run_fingerprint_agent(video["video_id"], records, api_key)
            for record in records:
                record.pop("fingerprint_error", None)
        except Exception as exc:
            for record in records:
                record["fingerprint_error"] = str(exc)[:500]
            identities = None
    if identities is None:
        roster = _consolidate_people(records)
        vehicles, plates, objects = _consolidate_things(records)
        identities = {
            "people": roster,
            "vehicles": vehicles,
            "plates": plates,
            "objects": objects,
        }
    else:
        roster = identities.get("people") or []
    _stitch_events(records, video["video_id"])
    _share_event_participants(records)
    from workers.roles import tag_suspects

    identities = tag_suspects(records, identities, api_key)
    roster = identities.get("people") or roster
    save_identities(video["video_id"], identities)
    save_roster(video["video_id"], roster)
    for record in records:
        attach_local_descriptions(record)
        save_clip_record(video["video_id"], record)
    return save_video_status(video, "fingerprinted")


def denoise_clip(record: dict, scene: dict | None = None) -> dict:
    scene = scene or scene_prior([record])
    kept = []
    for item in record.get("entities") or []:
        label = (item.get("label") or "").lower()
        kind = item.get("type")
        conf = float(item.get("confidence") or 0)
        item.pop("caption", None)
        if item.get("clothing_source") == "caption":
            item["clothing_source"] = "hsv"
        if scene.get("land") and label in RARE_ON_ROAD | WATER_LABELS:
            continue
        if kind == "object" and conf < 0.45:
            continue
        if kind == "vehicle" and label in WATER_LABELS and scene.get("land"):
            continue
        kept.append(item)
    record["entities"] = kept
    attach_local_descriptions(record)
    return record


def is_key_clip(record: dict) -> bool:
    signals = record.get("signals") or {}
    if float(signals.get("loud_impact") or 0) >= 0.7:
        return True
    if float(signals.get("yelling") or signals.get("loud_speech") or 0) >= 0.7:
        return True
    if int(signals.get("repeated_commands") or 0) >= 2:
        return True
    names = {
        *(item.get("definition") for item in record.get("keyword_hits") or []),
        *(record.get("candidate_definitions") or []),
        *(event.get("type") or event.get("definition") for event in record.get("events") or []),
    }
    return bool(names & KEY_EVENT_TYPES)


def should_ask_gemini(record: dict) -> bool:
    if is_key_clip(record):
        return True
    if record.get("events"):
        return True
    if record.get("person_count") or record.get("vehicle_count"):
        return True
    if record.get("keyword_hits"):
        return True
    return False


def scene_prior(records: list[dict]) -> dict:
    counts = Counter()
    for record in records:
        for item in record.get("entities") or []:
            label = (item.get("label") or "").lower()
            if label:
                counts[label] += 1
    land = sum(counts[label] for label in LAND_LABELS)
    water = sum(counts[label] for label in WATER_LABELS)
    return {
        "land": land >= water and land > 0,
        "labels": counts,
    }


def _consolidate_people(records: list[dict]) -> list[dict]:
    roster: list[dict] = []
    for record in records:
        remap = {}
        people = []
        others = []
        for item in record.get("entities") or []:
            if item.get("type") != "person":
                others.append(item)
                continue
            old_id = item.get("id") or ""
            payload = dict(item)
            if item.get("source") != "gemini":
                payload["id"] = ""
                payload["match"] = ""
            card = upsert_person(roster, payload, record.get("id") or "")
            remap[old_id] = card["id"]
            people.append(
                {
                    **item,
                    **card,
                    "id": card["id"],
                    "signature": card.get("signature") or make_signature(card),
                    "type": "person",
                }
            )
        record["entities"] = [*people, *others]
        for event in record.get("events") or []:
            event["people_ids"] = [remap.get(pid, pid) for pid in event.get("people_ids") or []]
    return roster


def _stitch_events(records: list[dict], video_id: str) -> None:
    last_by_type: dict[str, tuple[int, str]] = {}
    for record in records:
        index = int(record.get("index") or 0)
        clip_id = f"{video_id}:{record.get('id')}"
        for event in record.get("events") or []:
            type_id = event.get("type") or event.get("definition") or "event"
            event_id = f"{clip_id}:{type_id}:{event.get('start_ms', record.get('start_ms'))}"
            event["id"] = event_id
            event["type"] = type_id
            prev = last_by_type.get(type_id)
            if prev and prev[0] == index - 1:
                event["continues_from"] = prev[1]
            else:
                event.pop("continues_from", None)
            last_by_type[type_id] = (index, event_id)


def _share_event_participants(records: list[dict]) -> None:
    last_people: dict[str, list[str]] = {}
    last_vehicles: dict[str, list[str]] = {}
    last_objects: dict[str, list[str]] = {}
    for record in records:
        for event in record.get("events") or []:
            type_id = event.get("type") or event.get("definition") or ""
            people = [pid for pid in event.get("people_ids") or [] if pid]
            vehicles = [vid for vid in event.get("vehicle_ids") or [] if vid]
            objects = [oid for oid in event.get("object_ids") or [] if oid]
            if not people and type_id in last_people:
                event["people_ids"] = list(last_people[type_id])
            if not vehicles and type_id in last_vehicles:
                event["vehicle_ids"] = list(last_vehicles[type_id])
            if not objects and type_id in last_objects:
                event["object_ids"] = list(last_objects[type_id])
            if event.get("people_ids"):
                last_people[type_id] = event["people_ids"]
            if event.get("vehicle_ids"):
                last_vehicles[type_id] = event["vehicle_ids"]
            if event.get("object_ids"):
                last_objects[type_id] = event["object_ids"]


def _consolidate_things(records: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    vehicles: list[dict] = []
    plates: list[dict] = []
    objects: list[dict] = []
    for record in records:
        clip_id = record.get("id") or ""
        rewritten = []
        for item in record.get("entities") or []:
            kind = item.get("type")
            if kind == "vehicle":
                card = upsert_vehicle(vehicles, item, clip_id)
                rewritten.append({**item, **card, "type": "vehicle"})
                if card.get("plate"):
                    upsert_plate(plates, {"text": card["plate"], "vehicle_id": card["id"]}, clip_id)
                continue
            if kind == "object":
                card = upsert_object(objects, item, clip_id)
                rewritten.append({**item, **card, "type": "object"})
                continue
            rewritten.append(item)
        record["entities"] = rewritten
        for plate in record.get("plates") or []:
            if isinstance(plate, dict):
                upsert_plate(plates, plate, clip_id)
    return vehicles, plates, objects
