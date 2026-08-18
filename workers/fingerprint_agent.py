from __future__ import annotations

import json
import os
import urllib.request
from typing import Literal, TypedDict

from workers.roster import (
    public_objects,
    public_roster,
    public_vehicles,
    upsert_object,
    upsert_person,
    upsert_plate,
    upsert_vehicle,
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
FINGERPRINT_MODEL = os.environ.get("OPENROUTER_FINGERPRINT_MODEL", "google/gemini-3.5-flash")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_people",
            "description": "Search people already seen in this video by clothes, race, gender, hair, glasses, or officer vs civilian.",
            "parameters": {
                "type": "object",
                "properties": {
                    "clothes": {"type": "string"},
                    "race": {"type": "string"},
                    "gender": {"type": "string"},
                    "hair": {"type": "string"},
                    "glasses": {"type": "string"},
                    "is_cop": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_vehicles",
            "description": "Search vehicles already seen by color or plate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "color": {"type": "string"},
                    "plate": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_plates",
            "description": "Look up a license plate already seen in this video.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_objects",
            "description": "Search notable objects already seen (backpack, phone, bottle, weapon-like item).",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "distinctive": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_events",
            "description": "List recent events in earlier clips of this video, including who and which vehicles were involved.",
            "parameters": {"type": "object", "properties": {"type": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commit_identities",
            "description": "Commit identity matches for this clip. Call this once you have decided who is new vs already seen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "people": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "local_id": {"type": "string"},
                                "match": {"type": "string", "enum": ["existing", "new"]},
                                "id": {"type": "string"},
                                "race": {"type": "string"},
                                "gender": {"type": "string"},
                                "hair": {"type": "string"},
                                "glasses": {"type": "string"},
                                "clothes": {"type": "string"},
                                "shoes": {"type": "string"},
                                "bag": {"type": "string"},
                                "distinctive": {"type": "string"},
                                "description": {"type": "string"},
                                "is_cop": {"type": "boolean"},
                                "reason": {"type": "string"},
                            },
                            "required": ["match"],
                        },
                    },
                    "vehicles": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "local_id": {"type": "string"},
                                "match": {"type": "string", "enum": ["existing", "new"]},
                                "id": {"type": "string"},
                                "color": {"type": "string"},
                                "plate": {"type": "string"},
                                "analysis": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["match"],
                        },
                    },
                    "plates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "vehicle_id": {"type": "string"},
                                "confidence": {"type": "number"},
                            },
                            "required": ["text"],
                        },
                    },
                    "objects": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "local_id": {"type": "string"},
                                "match": {"type": "string", "enum": ["existing", "new"]},
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                                "distinctive": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["match"],
                        },
                    },
                    "event_links": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string"},
                                "people_ids": {"type": "array", "items": {"type": "string"}},
                                "vehicle_ids": {"type": "array", "items": {"type": "string"}},
                                "object_ids": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["type"],
                        },
                    },
                },
            },
        },
    },
]


class FingerprintState(TypedDict):
    video_id: str
    clips: list
    index: int
    people: list
    vehicles: list
    plates: list
    objects: list
    api_key: str
    model: str


def run_fingerprint_agent(video_id: str, clips: list[dict], api_key: str) -> dict:
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(FingerprintState)
    graph.add_node("resolve", _resolve_clip)
    graph.add_edge(START, "resolve")
    graph.add_conditional_edges("resolve", _route, {"resolve": "resolve", "done": END})
    app = graph.compile()
    state: FingerprintState = {
        "video_id": video_id,
        "clips": clips,
        "index": 0,
        "people": [],
        "vehicles": [],
        "plates": [],
        "objects": [],
        "api_key": api_key,
        "model": os.environ.get("OPENROUTER_FINGERPRINT_MODEL", FINGERPRINT_MODEL),
    }
    if not clips:
        return state
    return app.invoke(state, {"recursion_limit": max(32, len(clips) + 8)})


def _route(state: FingerprintState) -> Literal["resolve", "done"]:
    if state["index"] < len(state["clips"]):
        return "resolve"
    return "done"


def _resolve_clip(state: FingerprintState) -> dict:
    record = state["clips"][state["index"]]
    commit = _agent_commit(state, record)
    people = list(state["people"])
    vehicles = list(state["vehicles"])
    plates = list(state["plates"])
    objects = list(state["objects"])
    _apply_commit(record, commit, people, vehicles, plates, objects)
    return {
        "index": state["index"] + 1,
        "people": people,
        "vehicles": vehicles,
        "plates": plates,
        "objects": objects,
    }


def _agent_commit(state: FingerprintState, record: dict) -> dict:
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _user_payload(state, record)},
    ]
    splice = record.get("tagged_splice_uri") or record.get("splice_uri")
    if splice:
        from pathlib import Path
        import base64

        path = Path(splice)
        if path.exists() and path.stat().st_size > 32:
            encoded = base64.b64encode(path.read_bytes()).decode()
            messages[1]["content"] = [
                {"type": "text", "text": messages[1]["content"]},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                },
            ]
    for _ in range(8):
        body = _chat(state["api_key"], state["model"], messages, tools=True)
        message = body["choices"][0]["message"]
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            parsed = _parse_json(message.get("content") or "")
            if parsed:
                return parsed
            messages.append(
                {
                    "role": "user",
                    "content": "Call commit_identities now with your matches.",
                }
            )
            continue
        for call in calls:
            name = (call.get("function") or {}).get("name") or ""
            raw_args = (call.get("function") or {}).get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}
            if name == "commit_identities":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps({"ok": True}),
                    }
                )
                return args if isinstance(args, dict) else {}
            result = _run_tool(name, args if isinstance(args, dict) else {}, state, record)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(result),
                }
            )
    return {}


def _system_prompt() -> str:
    return (
        "You fingerprint identities across police bodycam clips for a graph. "
        "Decide whether each person, vehicle, plate, and notable object was already seen. "
        "Same clothes + hair, glasses, race, or gender can be the same person; unknown means not visible. "
        "Same plate is the same vehicle. Same color alone is not enough unless the shared event still has that car. "
        "Same distinctive object (bag color, phone in hand) can be reused. "
        "Use search_* tools against the running roster, then commit_identities. "
        "Reuse existing ids when it is the same entity. Create new ids only when new. "
        "Do not invent race or gender when not visible; use unknown. "
        "Observable facts only. No names, guilt, or charges. "
        "If this clip continues a shared event (traffic_stop, arrest, interrogation), "
        "put the same people, vehicles, and objects on event_links."
    )


def _user_payload(state: FingerprintState, record: dict) -> str:
    prior = []
    for clip in state["clips"][: state["index"]]:
        for event in clip.get("events") or []:
            prior.append(
                {
                    "clip": clip.get("id"),
                    "type": event.get("type") or event.get("definition"),
                    "clock": event.get("clock"),
                    "people_ids": event.get("people_ids") or [],
                    "vehicle_ids": event.get("vehicle_ids") or [],
                    "object_ids": event.get("object_ids") or [],
                }
            )
    return (
        f"Clip {record.get('id')} at {record.get('clock') or record.get('start_ms')} "
        f"({record.get('start_ms')}–{record.get('end_ms')} ms).\n"
        f"Summary: {(record.get('summary') or '')[:800]}\n"
        f"Transcript: {(record.get('transcript') or '')[:500]}\n"
        f"This clip entities: {json.dumps(_clip_entities(record))}\n"
        f"This clip events: {json.dumps(_clip_events(record))}\n"
        f"Known people: {json.dumps(public_roster(state['people']))}\n"
        f"Known vehicles: {json.dumps(public_vehicles(state['vehicles']))}\n"
        f"Known plates: {json.dumps(state['plates'])}\n"
        f"Known objects: {json.dumps(public_objects(state['objects']))}\n"
        f"Shared events so far: {json.dumps(prior[-16:])}\n"
        "Look at the splice if attached. Search the roster if unsure, then commit_identities."
    )


def _clip_entities(record: dict) -> list[dict]:
    rows = []
    for item in record.get("entities") or []:
        kind = item.get("type")
        if kind == "person":
            rows.append(
                {
                    "id": item.get("id"),
                    "type": "person",
                    "race": item.get("race"),
                    "gender": item.get("gender"),
                    "hair": item.get("hair"),
                    "glasses": item.get("glasses"),
                    "clothes": item.get("clothes"),
                    "description": item.get("description"),
                    "is_cop": item.get("is_cop"),
                }
            )
        elif kind == "vehicle":
            rows.append(
                {
                    "id": item.get("id"),
                    "type": "vehicle",
                    "color": item.get("color"),
                    "plate": item.get("plate"),
                    "label": item.get("label"),
                    "analysis": item.get("analysis"),
                }
            )
        else:
            rows.append(
                {
                    "id": item.get("id"),
                    "type": "object",
                    "label": item.get("label"),
                    "distinctive": item.get("distinctive") or item.get("image_tag"),
                }
            )
    return rows


def _clip_events(record: dict) -> list[dict]:
    rows = []
    for event in record.get("events") or []:
        rows.append(
            {
                "type": event.get("type") or event.get("definition"),
                "clock": event.get("clock"),
                "start_ms": event.get("start_ms"),
                "people_ids": event.get("people_ids") or [],
                "vehicle_ids": event.get("vehicle_ids") or [],
                "object_ids": event.get("object_ids") or [],
                "summary": (event.get("summary") or "")[:240],
            }
        )
    return rows


def _run_tool(name: str, args: dict, state: FingerprintState, record: dict):
    if name == "search_people":
        return _filter_people(state["people"], args)
    if name == "search_vehicles":
        return _filter_vehicles(state["vehicles"], args)
    if name == "search_plates":
        needle = str(args.get("text") or "").upper()
        return [item for item in state["plates"] if needle and needle in str(item.get("text") or "")]
    if name == "search_objects":
        label = str(args.get("label") or "").lower()
        mark = str(args.get("distinctive") or "").lower()
        hits = []
        for item in state["objects"]:
            if label and label not in str(item.get("label") or "").lower():
                continue
            if mark and mark not in str(item.get("distinctive") or "").lower():
                continue
            hits.append(item)
        return hits
    if name == "search_events":
        wanted = str(args.get("type") or "").strip()
        hits = []
        for clip in state["clips"][: state["index"]]:
            for event in clip.get("events") or []:
                type_id = event.get("type") or event.get("definition") or ""
                if wanted and type_id != wanted:
                    continue
                hits.append(
                    {
                        "clip": clip.get("id"),
                        "type": type_id,
                        "clock": event.get("clock"),
                        "people_ids": event.get("people_ids") or [],
                        "vehicle_ids": event.get("vehicle_ids") or [],
                        "object_ids": event.get("object_ids") or [],
                    }
                )
        return hits[-20:]
    return {"error": f"unknown tool {name}"}


def _filter_people(people: list[dict], args: dict) -> list[dict]:
    hits = []
    for item in people:
        if args.get("is_cop") is True and item.get("is_cop") is not True:
            continue
        if args.get("is_cop") is False and item.get("is_cop") is True:
            continue
        ok = True
        for key in ("clothes", "race", "gender", "hair", "glasses"):
            needle = str(args.get(key) or "").lower().strip()
            if not needle or needle == "unknown":
                continue
            value = str(item.get(key) or "").lower()
            if needle not in value and value not in needle:
                ok = False
                break
        if ok:
            hits.append(item)
    return public_roster(hits)


def _filter_vehicles(vehicles: list[dict], args: dict) -> list[dict]:
    color = str(args.get("color") or "").lower().strip()
    plate = "".join(ch for ch in str(args.get("plate") or "").upper() if ch.isalnum())
    hits = []
    for item in vehicles:
        if plate and plate != str(item.get("plate") or ""):
            continue
        if color and color not in str(item.get("color") or "").lower():
            continue
        hits.append(item)
    return public_vehicles(hits)


def _apply_commit(
    record: dict,
    commit: dict,
    people: list[dict],
    vehicles: list[dict],
    plates: list[dict],
    objects: list[dict],
) -> None:
    clip_id = record.get("id") or ""
    person_map = {}
    vehicle_map = {}
    object_map = {}
    for item in commit.get("people") or []:
        payload = dict(item)
        if item.get("match") == "existing" and item.get("id"):
            payload["id"] = item["id"]
            payload["match"] = "existing"
        else:
            payload["id"] = ""
            payload["match"] = "new"
        card = upsert_person(people, payload, clip_id)
        local_id = item.get("local_id") or item.get("id") or ""
        if local_id:
            person_map[local_id] = card["id"]
        person_map[card["id"]] = card["id"]
    for item in commit.get("vehicles") or []:
        payload = dict(item)
        if item.get("match") != "existing":
            payload["id"] = item.get("id") if str(item.get("id") or "").startswith("vehicle_") else ""
        card = upsert_vehicle(vehicles, payload, clip_id)
        local_id = item.get("local_id") or item.get("id") or ""
        if local_id:
            vehicle_map[local_id] = card["id"]
        vehicle_map[card["id"]] = card["id"]
        if card.get("plate"):
            upsert_plate(plates, {"text": card["plate"], "vehicle_id": card["id"]}, clip_id)
    for item in commit.get("plates") or []:
        card = upsert_plate(plates, item, clip_id)
        if card and item.get("vehicle_id"):
            vehicle_map[item["vehicle_id"]] = item.get("vehicle_id")
    for item in commit.get("objects") or []:
        payload = dict(item)
        if item.get("match") != "existing":
            payload["id"] = ""
        card = upsert_object(objects, payload, clip_id)
        local_id = item.get("local_id") or item.get("id") or ""
        if local_id:
            object_map[local_id] = card["id"]
        object_map[card["id"]] = card["id"]
    if not person_map:
        for item in record.get("entities") or []:
            if item.get("type") != "person":
                continue
            card = upsert_person(people, {**item, "id": "", "match": ""}, clip_id)
            person_map[item.get("id") or card["id"]] = card["id"]
    if not vehicle_map:
        for item in record.get("entities") or []:
            if item.get("type") != "vehicle":
                continue
            card = upsert_vehicle(vehicles, {**item, "id": "", "match": ""}, clip_id)
            vehicle_map[item.get("id") or card["id"]] = card["id"]
            if card.get("plate"):
                upsert_plate(plates, {"text": card["plate"], "vehicle_id": card["id"]}, clip_id)
    if not object_map:
        for item in record.get("entities") or []:
            if item.get("type") != "object":
                continue
            card = upsert_object(objects, {**item, "id": "", "match": ""}, clip_id)
            object_map[item.get("id") or card["id"]] = card["id"]
    _rewrite_clip(record, people, vehicles, plates, objects, person_map, vehicle_map, object_map, commit)


def _rewrite_clip(
    record: dict,
    people: list[dict],
    vehicles: list[dict],
    plates: list[dict],
    objects: list[dict],
    person_map: dict,
    vehicle_map: dict,
    object_map: dict,
    commit: dict,
) -> None:
    by_person = {item["id"]: item for item in people}
    by_vehicle = {item["id"]: item for item in vehicles}
    by_object = {item["id"]: item for item in objects}
    rewritten = []
    seen = set()
    for item in record.get("entities") or []:
        kind = item.get("type")
        old = item.get("id") or ""
        if kind == "person":
            new_id = person_map.get(old) or person_map.get(item.get("local_id") or "")
            if not new_id:
                continue
            card = by_person.get(new_id) or item
            if new_id in seen:
                continue
            seen.add(new_id)
            rewritten.append({**item, **card, "id": new_id, "type": "person", "source": "fingerprint"})
            continue
        if kind == "vehicle":
            new_id = vehicle_map.get(old) or old
            card = by_vehicle.get(new_id) or item
            rewritten.append({**item, **card, "id": new_id, "type": "vehicle", "source": "fingerprint"})
            continue
        if kind == "object":
            new_id = object_map.get(old) or old
            card = by_object.get(new_id) or item
            rewritten.append({**item, **card, "id": new_id, "type": "object", "source": "fingerprint"})
            continue
        rewritten.append(item)
    for person in people:
        if person["id"] in seen:
            continue
        if record.get("id") in (person.get("clips") or []) or person.get("last_clip") == record.get("id"):
            rewritten.insert(0, {**person, "type": "person", "source": "fingerprint"})
            seen.add(person["id"])
    record["entities"] = rewritten
    record["plates"] = [
        {"text": item.get("text"), "vehicle_id": item.get("vehicle_id"), "confidence": item.get("confidence") or 0.6}
        for item in plates
        if record.get("id") in (item.get("clips") or [])
    ]
    links = {item.get("type"): item for item in commit.get("event_links") or [] if item.get("type")}
    for event in record.get("events") or []:
        type_id = event.get("type") or event.get("definition")
        event["people_ids"] = [_remap(pid, person_map) for pid in event.get("people_ids") or []]
        event["vehicle_ids"] = [_remap(vid, vehicle_map) for vid in event.get("vehicle_ids") or []]
        link = links.get(type_id)
        if link:
            if link.get("people_ids"):
                event["people_ids"] = [_remap(pid, person_map) for pid in link["people_ids"]]
            if link.get("vehicle_ids"):
                event["vehicle_ids"] = [_remap(vid, vehicle_map) for vid in link["vehicle_ids"]]
            if link.get("object_ids"):
                event["object_ids"] = [_remap(oid, object_map) for oid in link["object_ids"]]


def _remap(value: str, mapping: dict) -> str:
    return mapping.get(value, value)


def _chat(api_key: str, model: str, messages: list, tools: bool = False) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    if tools:
        payload["tools"] = TOOLS
        payload["tool_choice"] = "auto"
    else:
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:8080",
            "X-Title": "video-intel-fingerprint",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode())


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
