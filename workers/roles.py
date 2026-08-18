from __future__ import annotations

import json
import os
import urllib.request

from workers.clock import format_clock

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SUBJECT_EVENTS = {
    "traffic_stop",
    "arrest",
    "handcuffing",
    "physical_restraint",
    "miranda_warning",
    "interrogation",
    "field_sobriety_test",
    "search_person",
    "verbal_escalation",
    "foot_pursuit",
}


def tag_suspects(records: list[dict], identities: dict, api_key: str = "") -> dict:
    people = identities.get("people") or []
    assignments = []
    if api_key:
        try:
            assignments = _agent_roles(records, people, api_key)
        except Exception:
            assignments = []
    if not assignments:
        assignments = _heuristic_roles(records, people)
    _apply_roles(people, records, assignments)
    identities["people"] = people
    return identities


def _agent_roles(records: list[dict], people: list[dict], api_key: str) -> list[dict]:
    model = os.environ.get("OPENROUTER_FINGERPRINT_MODEL", "google/gemini-3.5-flash")
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_payload(records, people)},
        ],
    }
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:8080",
            "X-Title": "video-intel-roles",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        body = json.loads(response.read().decode())
    text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    data = _parse_json(text) or {}
    rows = data.get("potential_suspects") or data.get("people") or []
    return [item for item in rows if isinstance(item, dict) and item.get("id")]


def _heuristic_roles(records: list[dict], people: list[dict]) -> list[dict]:
    by_id = {item.get("id"): item for item in people}
    found: dict[str, dict] = {}
    for record in records:
        for event in record.get("events") or []:
            type_id = event.get("type") or event.get("definition") or ""
            if type_id not in SUBJECT_EVENTS:
                continue
            clock = event.get("clock") or event.get("start_timestamp") or format_clock(event.get("start_ms"))
            quote = _transcript_at(record, event)
            for pid in event.get("people_ids") or []:
                person = by_id.get(pid) or {}
                if person.get("is_cop") is True:
                    continue
                if person.get("is_cop") is not False and not _looks_civilian(person):
                    continue
                if pid in found:
                    continue
                found[pid] = {
                    "id": pid,
                    "event_type": type_id,
                    "at": clock,
                    "reason": (
                        f"{type_id.replace('_', ' ')} at {clock}: officer is addressing this "
                        f"civilian. Transcript: \"{quote[:180]}\""
                        if quote
                        else f"{type_id.replace('_', ' ')} at {clock}: only civilian on this event."
                    ),
                }
    return list(found.values())


def _apply_roles(people: list[dict], records: list[dict], assignments: list[dict]) -> None:
    by_id = {item.get("id"): item for item in people}
    tagged = {}
    for item in assignments:
        pid = item.get("id")
        person = by_id.get(pid)
        if not person or person.get("is_cop") is True:
            continue
        person["potential_suspect"] = True
        person["role"] = "potential_suspect"
        person["suspect_reason"] = (item.get("reason") or "").strip()
        person["suspect_at"] = item.get("at") or ""
        person["suspect_event"] = item.get("event_type") or ""
        person["suspect_snapshot"] = _snapshot(person)
        tagged[pid] = item
    for person in people:
        if person.get("id") in tagged:
            continue
        person.setdefault("potential_suspect", False)
        if person.get("is_cop") is True:
            person["role"] = "officer"
        elif person.get("role") != "potential_suspect":
            person["role"] = "civilian"
    for record in records:
        for event in record.get("events") or []:
            type_id = event.get("type") or event.get("definition") or ""
            roles = {}
            subjects = []
            for pid in event.get("people_ids") or []:
                person = by_id.get(pid) or {}
                role = _event_role(person, type_id)
                roles[pid] = role
                if role == "potential_suspect":
                    subjects.append(pid)
            event["people_roles"] = roles
            event["subject_ids"] = subjects
        for entity in record.get("entities") or []:
            if entity.get("type") != "person":
                continue
            card = by_id.get(entity.get("id"))
            if not card:
                continue
            entity["potential_suspect"] = bool(card.get("potential_suspect"))
            entity["role"] = card.get("role")
            entity["suspect_reason"] = card.get("suspect_reason") or ""
            entity["suspect_at"] = card.get("suspect_at") or ""
            entity["suspect_event"] = card.get("suspect_event") or ""
            entity["suspect_snapshot"] = card.get("suspect_snapshot") or ""


def _event_role(person: dict, type_id: str) -> str:
    if person.get("is_cop") is True:
        return "officer"
    if person.get("potential_suspect") and type_id in SUBJECT_EVENTS:
        return "potential_suspect"
    if person.get("is_cop") is False:
        return "civilian"
    return "unknown"


def _snapshot(person: dict) -> str:
    parts = [person.get("description") or ""]
    clothes = person.get("clothes") or ""
    if clothes and clothes not in parts[0]:
        parts.append(clothes)
    mark = person.get("distinctive") or ""
    if mark and mark not in " ".join(parts):
        parts.append(mark)
    return " | ".join(part for part in parts if part).strip()


def _looks_civilian(person: dict) -> bool:
    clothes = (person.get("clothes") or "").lower()
    if "police" in clothes or "uniform" in clothes or "badge" in clothes:
        return False
    return bool(clothes or person.get("description"))


def _transcript_at(record: dict, event: dict) -> str:
    start = int(event.get("start_ms") or record.get("start_ms") or 0)
    clip_start = int(record.get("start_ms") or 0)
    rel = max(start - clip_start, 0)
    window = 20000
    hits = []
    for item in record.get("transcript_segments") or []:
        seg_start = int(item.get("start_ms") or 0)
        if abs(seg_start - rel) <= window or rel <= seg_start <= rel + window:
            text = (item.get("text") or "").strip()
            if text:
                hits.append(text)
        if len(hits) >= 4:
            break
    if hits:
        return " ".join(hits)
    return (event.get("transcript") or record.get("transcript") or "").strip()[:240]


def _system_prompt() -> str:
    return (
        "You assign investigative roles from a police bodycam, not guilt or charges. "
        "potential_suspect means the civilian the officer is stopping, arresting, cuffing, "
        "Mirandizing, or questioning as the subject of this interaction. "
        "Use event type + start timestamp + the people present at that timestamp + the transcript. "
        "If the officer says 'step outside' or 'you are under arrest' or reads Miranda, "
        "the civilian they are talking to is the potential_suspect. Snapshot their description. "
        "Never tag a uniformed officer as potential_suspect. "
        "Do not invent names, race, or gender. Observable facts only. "
        'Return JSON: {"potential_suspects":[{"id":str,"event_type":str,"at":str,"reason":str}]}'
    )


def _user_payload(records: list[dict], people: list[dict]) -> str:
    roster = []
    for item in people:
        roster.append(
            {
                "id": item.get("id"),
                "is_cop": item.get("is_cop"),
                "clothes": item.get("clothes"),
                "description": item.get("description"),
                "race": item.get("race"),
                "gender": item.get("gender"),
            }
        )
    events = []
    for record in records:
        for event in record.get("events") or []:
            type_id = event.get("type") or event.get("definition") or ""
            if type_id not in SUBJECT_EVENTS:
                continue
            present = []
            for pid in event.get("people_ids") or []:
                person = next((item for item in people if item.get("id") == pid), None) or {"id": pid}
                present.append(
                    {
                        "id": person.get("id"),
                        "is_cop": person.get("is_cop"),
                        "clothes": person.get("clothes"),
                        "description": (person.get("description") or "")[:180],
                    }
                )
            events.append(
                {
                    "clip": record.get("id"),
                    "type": type_id,
                    "start_timestamp": event.get("clock")
                    or event.get("start_timestamp")
                    or format_clock(event.get("start_ms")),
                    "people_present": present,
                    "transcript": _transcript_at(record, event)[:320],
                }
            )
    return (
        f"People: {json.dumps(roster)}\n"
        f"Events with people at that timestamp: {json.dumps(events[:24])}\n"
        "Tag who the officer is looking at / talking to as potential_suspect."
    )


def _parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
