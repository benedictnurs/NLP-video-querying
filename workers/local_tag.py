from __future__ import annotations

from pathlib import Path


def attach_local_descriptions(record: dict) -> dict:
    entities = record.get("entities") or []
    people = [item for item in entities if item.get("type") == "person"]
    vehicles = [item for item in entities if item.get("type") == "vehicle"]
    objects = [item for item in entities if item.get("type") == "object"]
    record["people"] = people
    record["vehicles"] = vehicles
    record["objects"] = objects
    record["person_count"] = len(people)
    record["vehicle_count"] = len(vehicles)
    record["object_count"] = len(objects)
    record["people_descriptions"] = [_person_line(item) for item in people]
    record["object_labels"] = sorted(
        {
            item.get("label")
            for item in [*objects, *vehicles]
            if item.get("label")
        }
    )
    record["clothing_colors"] = sorted(
        {
            item.get("clothes")
            for item in people
            if item.get("clothes")
        }
    )
    record["plates"] = [
        item.get("plate")
        for item in vehicles
        if item.get("plate")
    ]
    record["needs_vision"] = _needs_vision(record)
    record["vision_uris"] = _vision_uris(record)
    return record


def _person_line(person: dict) -> str:
    signature = (person.get("signature") or "").strip()
    if signature:
        return f"{person.get('id')}: {signature}"
    clothes = (person.get("clothes") or "").strip()
    description = (person.get("description") or "").strip()
    return f"{person.get('id')}: {clothes or description or 'person'}"


def _needs_vision(record: dict) -> bool:
    people = record.get("people") or []
    vehicles = record.get("vehicles") or []
    objects = record.get("objects") or []
    if people or vehicles:
        return True
    if any((item.get("confidence") or 0) < 0.5 for item in objects):
        return True
    if any(item.get("label") in {"knife", "cell phone", "backpack", "suitcase"} for item in objects):
        return True
    return False


def _vision_uris(record: dict) -> list[str]:
    uris: list[str] = []
    for key in ("tagged_splice_uri", "splice_uri"):
        path = record.get(key)
        if path and Path(path).exists() and path not in uris:
            uris.append(path)
            break
    for person in record.get("people") or []:
        for key in ("crop_uri", "tagged_frame_uri", "frame_uri"):
            path = person.get(key)
            if path and Path(path).exists() and path not in uris:
                uris.append(path)
                break
    for vehicle in record.get("vehicles") or []:
        for key in ("plate_crop_uri", "frame_uri"):
            path = vehicle.get(key)
            if path and Path(path).exists() and path not in uris:
                uris.append(path)
                break
    for item in record.get("objects") or []:
        if (item.get("confidence") or 1) >= 0.5 and item.get("label") not in {
            "knife",
            "cell phone",
            "backpack",
            "suitcase",
        }:
            continue
        path = item.get("frame_uri")
        if path and Path(path).exists() and path not in uris:
            uris.append(path)
    return uris[:6]
