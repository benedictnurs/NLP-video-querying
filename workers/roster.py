from __future__ import annotations

import json
from pathlib import Path

from workers.paths import video_work_dir

SIGN_KEYS = ("gender", "hair", "glasses", "clothes", "shoes", "bag", "distinctive")


def roster_path(video_id: str) -> Path:
    return video_work_dir(video_id) / "people.json"


def load_roster(video_id: str) -> list[dict]:
    path = roster_path(video_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("people") or []
    people = [item for item in data if isinstance(item, dict) and item.get("id")]
    for item in people:
        item["signature"] = make_signature(item)
    return people


def save_roster(video_id: str, people: list[dict]) -> Path:
    path = roster_path(video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(people, indent=2) + "\n")
    return path


def public_roster(people: list[dict]) -> list[dict]:
    public = []
    for item in people:
        public.append(
            {
                "id": item.get("id"),
                "signature": item.get("signature") or make_signature(item),
                "gender": item.get("gender") or "unknown",
                "hair": item.get("hair") or "unknown",
                "glasses": item.get("glasses") or "unknown",
                "clothes": item.get("clothes") or "",
                "is_cop": item.get("is_cop"),
                "description": item.get("description") or "",
                "last_clip": item.get("last_clip"),
            }
        )
    return public


def make_signature(person: dict) -> str:
    parts = []
    for key in SIGN_KEYS:
        value = _clean(person.get(key), "")
        if value and value != "unknown":
            parts.append(value)
    return " | ".join(parts)


def upsert_person(roster: list[dict], person: dict, clip_id: str) -> dict:
    incoming = _normalize_card(person)
    person_id = incoming["id"]
    match = (person.get("match") or "").strip().lower()
    existing = _by_id(roster, person_id) if person_id else None
    if existing is None and match != "new":
        existing = match_signature(roster, incoming)
    if existing is None:
        incoming["id"] = person_id if person_id.startswith("person_") else _next_id(roster)
        incoming["signature"] = make_signature(incoming)
        incoming["first_clip"] = clip_id
        incoming["last_clip"] = clip_id
        incoming["clips"] = [clip_id]
        roster.append(incoming)
        return incoming
    _fill(existing, incoming)
    existing["signature"] = make_signature(existing)
    existing["last_clip"] = clip_id
    clips = existing.setdefault("clips", [])
    if clip_id not in clips:
        clips.append(clip_id)
    return existing


def match_signature(roster: list[dict], person: dict) -> dict | None:
    incoming_sig = make_signature(person)
    if not incoming_sig:
        return None
    best = None
    best_score = 0
    incoming_cop = person.get("is_cop")
    for item in roster:
        known_cop = item.get("is_cop")
        if incoming_cop is True and known_cop is False:
            continue
        if incoming_cop is False and known_cop is True:
            continue
        score = _signature_score(person, item)
        if score > best_score:
            best = item
            best_score = score
    if best_score >= 3:
        return best
    if best_score >= 2 and _has_unique_mark(person, best or {}):
        return best
    return None


def _signature_score(left: dict, right: dict) -> int:
    score = 0
    for key in SIGN_KEYS:
        a = _clean(left.get(key), "")
        b = _clean(right.get(key), "")
        if not a or not b or a == "unknown" or b == "unknown":
            continue
        if a == b or a in b or b in a:
            score += 2 if key in {"hair", "glasses", "distinctive", "clothes"} else 1
    return score


def _has_unique_mark(left: dict, right: dict) -> bool:
    for key in ("glasses", "hair", "distinctive"):
        a = _clean(left.get(key), "")
        b = _clean(right.get(key), "")
        if a and b and a != "unknown" and (a == b or a in b or b in a):
            clothes_a = _clean(left.get("clothes"), "")
            clothes_b = _clean(right.get("clothes"), "")
            if clothes_a and clothes_b and (clothes_a in clothes_b or clothes_b in clothes_a):
                return True
    return False


def _normalize_card(person: dict) -> dict:
    return {
        "id": (person.get("id") or "").strip(),
        "race": _clean(person.get("race"), "unknown"),
        "gender": _clean(person.get("gender"), "unknown"),
        "hair": _clean(person.get("hair"), "unknown"),
        "glasses": _clean(person.get("glasses"), "unknown"),
        "clothes": (person.get("clothes") or "").strip(),
        "shoes": _clean(person.get("shoes"), "unknown"),
        "bag": _clean(person.get("bag"), "unknown"),
        "distinctive": (person.get("distinctive") or "").strip(),
        "description": (person.get("description") or "").strip(),
        "is_cop": _as_bool(person.get("is_cop"), person.get("role")),
    }


def _by_id(roster: list[dict], person_id: str) -> dict | None:
    for item in roster:
        if item.get("id") == person_id:
            return item
    return None


def _next_id(roster: list[dict]) -> str:
    numbers = []
    for item in roster:
        raw = str(item.get("id") or "").replace("person_", "")
        if raw.isdigit():
            numbers.append(int(raw))
    return f"person_{max(numbers, default=0) + 1}"


def _fill(existing: dict, incoming: dict) -> None:
    if incoming.get("is_cop") is True:
        existing["is_cop"] = True
    elif existing.get("is_cop") is None and incoming.get("is_cop") is False:
        existing["is_cop"] = False
    for key in ("race", "gender", "hair", "glasses", "clothes", "shoes", "bag", "distinctive", "description"):
        value = incoming.get(key)
        if not value or value == "unknown":
            continue
        current = existing.get(key)
        if not current or current == "unknown":
            existing[key] = value
        elif key in {"clothes", "description", "distinctive", "hair"} and len(str(value)) > len(str(current)):
            existing[key] = value


def _as_bool(value, role=None) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    role_text = str(role or "").strip().lower()
    if text in {"true", "yes", "officer", "cop", "police"} or role_text in {"officer", "cop", "police"}:
        return True
    if text in {"false", "no", "civilian"} or role_text in {"civilian"}:
        return False
    return None


def _clean(value, default: str) -> str:
    text = str(value or "").strip().lower()
    if not text or text in {"null", "n/a", "unreadable"}:
        return default
    if text in {"none", "no", "no glasses"}:
        return "none"
    return text
