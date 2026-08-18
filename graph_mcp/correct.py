from __future__ import annotations

from datetime import datetime, timezone

from graph_mcp.db import run_cypher, run_write
from workers.clock import format_clock, parse_clock

KINDS = ("person", "vehicle", "event", "object", "plate", "clip")
ROLES = ("officer", "civilian", "potential_suspect", "unknown")

ALLOWED = {
    "person": {
        "clothes",
        "race",
        "gender",
        "hair",
        "glasses",
        "shoes",
        "bag",
        "distinctive",
        "description",
        "signature",
        "is_cop",
        "role",
        "potential_suspect",
        "suspect_reason",
        "suspect_snapshot",
    },
    "vehicle": {"color", "plate", "analysis"},
    "event": {
        "type",
        "title",
        "summary",
        "analysis",
        "start_timestamp",
        "end_timestamp",
        "cell",
    },
    "object": {"label", "distinctive"},
    "plate": {"text"},
    "clip": {"summary"},
}

_LABEL = {
    "person": "Person",
    "vehicle": "Vehicle",
    "event": "Event",
    "object": "Object",
    "plate": "Plate",
    "clip": "Clip",
}


def apply_correction(
    *,
    user_request: str,
    kind: str,
    confirmed: bool,
    node_id: str = "",
    video_id: str = "",
    start_timestamp: str = "",
    event_type: str = "",
    involves_person: str = "",
    involves_role: str = "",
    changes: dict | None = None,
) -> dict:
    request = (user_request or "").strip()
    if len(request) < 8:
        return {
            "error": "user_request is required. Quote the user's ask or correction. Do not invent a reason.",
        }
    kind_key = (kind or "").strip().lower()
    if kind_key not in KINDS:
        return {"error": f"kind must be one of {list(KINDS)}", "kind": kind}

    patch = {key: value for key, value in (changes or {}).items() if value is not None and value != ""}
    if involves_role.strip():
        patch["_involves_role"] = involves_role.strip()
        patch["_involves_person"] = involves_person.strip()
    if not patch:
        return {
            "error": "No fields to change.",
            "allowed": sorted(ALLOWED[kind_key]),
            "hint": "Pass clothes, role, is_cop, type, start_timestamp, plate, etc.",
        }

    cleaned, err = _clean_patch(kind_key, patch)
    if err:
        return err

    found = _find(
        kind_key,
        node_id=node_id.strip(),
        video_id=video_id.strip(),
        start_timestamp=start_timestamp.strip(),
        event_type=event_type.strip(),
        involves_person=involves_person.strip(),
    )
    if found.get("error"):
        return found
    rows = found["rows"]
    if not rows:
        return {
            "error": "No matching node. Pass id (person_3 or video_1:person_3) and video_id; for events also type + start_timestamp.",
            "kind": kind_key,
        }
    if len(rows) > 1:
        return {
            "error": "Multiple matches. Pass video_id (and start_timestamp for events).",
            "matches": [_public(kind_key, row) for row in rows[:8]],
        }

    before = rows[0]
    props = {key: cleaned[key] for key in cleaned if not key.startswith("_")}
    preview = {
        "user_request": request,
        "kind": kind_key,
        "id": before.get("id"),
        "before": _public(kind_key, before),
        "set": props,
        "wrote": False,
    }
    if "_involves_role" in cleaned:
        preview["involves"] = {
            "person": cleaned.get("_involves_person") or involves_person,
            "role": cleaned["_involves_role"],
        }
    if not confirmed:
        preview["hint"] = (
            "Preview only. Set confirmed=true ONLY if the user asked to change the graph "
            "or corrected this fact. Do not write after analyzing a scene on your own."
        )
        return preview

    written = _write(kind_key, before, cleaned, request)
    after = _find(
        kind_key,
        node_id=before.get("id") or node_id,
        video_id=before.get("video_id") or video_id,
        start_timestamp=cleaned.get("start_timestamp") or start_timestamp,
        event_type=cleaned.get("type") or event_type,
        involves_person=involves_person,
    )
    preview["wrote"] = True
    preview["write"] = written
    preview["after"] = _public(kind_key, (after.get("rows") or [before])[0])
    return preview


def _clean_patch(kind: str, patch: dict) -> tuple[dict, dict | None]:
    allowed = ALLOWED[kind]
    cleaned = {}
    for key, value in patch.items():
        if key.startswith("_"):
            continue
        if key not in allowed:
            return {}, {
                "error": f"Cannot set {key} on {kind}.",
                "allowed": sorted(allowed),
            }
        parsed, err = _coerce(kind, key, value)
        if err:
            return {}, err
        cleaned[key] = parsed
    if "role" in cleaned and cleaned["role"] not in ROLES:
        return {}, {"error": f"role must be one of {list(ROLES)}"}
    if cleaned.get("role") == "officer" and "is_cop" not in cleaned:
        cleaned["is_cop"] = True
    if cleaned.get("role") == "civilian" and "is_cop" not in cleaned:
        cleaned["is_cop"] = False
    if cleaned.get("role") == "potential_suspect" and "potential_suspect" not in cleaned:
        cleaned["potential_suspect"] = True
    if cleaned.get("is_cop") is True and "role" not in cleaned:
        cleaned["role"] = "officer"
    if "start_timestamp" in cleaned:
        ms = parse_clock(cleaned["start_timestamp"])
        if ms is None:
            return {}, {"error": "start_timestamp must look like 01:56"}
        cleaned["start_timestamp"] = format_clock(ms)
        cleaned["seek_s"] = round(ms / 1000.0, 3)
    if "end_timestamp" in cleaned:
        ms = parse_clock(cleaned["end_timestamp"])
        if ms is None:
            return {}, {"error": "end_timestamp must look like 01:56"}
        cleaned["end_timestamp"] = format_clock(ms)
    if "type" in cleaned:
        type_id = str(cleaned["type"]).strip().lower().replace(" ", "_")
        if not type_id.replace("_", "").isalnum():
            return {}, {"error": "event type must be a snake_case catalog id"}
        cleaned["type"] = type_id
        cleaned["definition"] = type_id
    if "color" in cleaned:
        cleaned["color"] = str(cleaned["color"]).strip().lower()
    if "plate" in cleaned or (kind == "plate" and "text" in cleaned):
        key = "plate" if "plate" in cleaned else "text"
        cleaned[key] = str(cleaned[key]).strip().upper()
    role = str(patch.get("_involves_role") or "").strip()
    if role:
        if role not in ROLES:
            return {}, {"error": f"involves_role must be one of {list(ROLES)}"}
        cleaned["_involves_role"] = role
        cleaned["_involves_person"] = str(patch.get("_involves_person") or "").strip()
    return cleaned, None


def _coerce(kind: str, key: str, value):
    if key in {"is_cop", "potential_suspect"}:
        if isinstance(value, bool):
            return value, None
        text = str(value).strip().lower()
        if text in {"true", "yes", "1"}:
            return True, None
        if text in {"false", "no", "0"}:
            return False, None
        if text in {"unknown", "none", ""}:
            return None, None
        return None, {"error": f"{key} must be true, false, or unknown"}
    if key == "cell":
        try:
            return int(value), None
        except (TypeError, ValueError):
            return None, {"error": "cell must be an integer"}
    return value, None


def _find(
    kind: str,
    *,
    node_id: str,
    video_id: str,
    start_timestamp: str,
    event_type: str,
    involves_person: str,
) -> dict:
    label = _LABEL[kind]
    qualified = node_id
    if video_id and node_id and ":" not in node_id:
        qualified = f"{video_id}:{node_id}"
    if kind == "clip" and video_id and node_id and not node_id.startswith(video_id):
        qualified = f"{video_id}:{node_id}"
    clock = ""
    seek_s = None
    if start_timestamp:
        ms = parse_clock(start_timestamp)
        if ms is not None:
            clock = format_clock(ms)
            seek_s = round(ms / 1000.0, 3)
        else:
            clock = start_timestamp

    if kind == "person" and not node_id:
        return {"error": "Pass id=person_3 (and video_id if more than one video)."}
    if kind == "event" and not node_id and not event_type:
        return {"error": "For events pass id, or event_type plus start_timestamp."}

    if kind == "event":
        rows = run_cypher(
            """
            MATCH (e:Event)
            WHERE ($id <> '' AND (e.id = $id OR e.id = $qualified))
               OR ($id = '' AND $type <> '' AND e.type = $type
                   AND ($clock = '' OR e.start_timestamp = $clock OR e.seek_s = $seek_s)
                   AND ($video_id = '' OR e.video_id = $video_id))
            RETURN e
            """,
            {
                "id": node_id,
                "qualified": qualified,
                "type": event_type,
                "clock": clock,
                "seek_s": seek_s,
                "video_id": video_id,
            },
            limit=10,
        )
        return {"rows": [row["e"] for row in rows if row.get("e")]}

    if kind == "person":
        rows = run_cypher(
            """
            MATCH (p:Person)
            WHERE p.id = $qualified
               OR p.id = $id
               OR (p.local_id = $id AND ($video_id = '' OR p.video_id = $video_id))
            RETURN p
            """,
            {"id": node_id, "qualified": qualified, "video_id": video_id},
            limit=10,
        )
        return {"rows": [row["p"] for row in rows if row.get("p")]}

    if kind == "vehicle":
        rows = run_cypher(
            """
            MATCH (v:Vehicle)
            WHERE v.id = $qualified OR v.id = $id
               OR ($id <> '' AND toUpper(v.plate) = toUpper($id))
               OR ($video_id <> '' AND v.video_id = $video_id AND v.id ENDS WITH ':' + $id)
            RETURN v
            """,
            {"id": node_id, "qualified": qualified, "video_id": video_id},
            limit=10,
        )
        return {"rows": [row["v"] for row in rows if row.get("v")]}

    if kind == "plate":
        rows = run_cypher(
            """
            MATCH (p:Plate)
            WHERE p.id = $qualified OR p.id = $id
               OR ($id <> '' AND toUpper(p.text) = toUpper($id))
            RETURN p
            """,
            {"id": node_id, "qualified": qualified},
            limit=10,
        )
        return {"rows": [row["p"] for row in rows if row.get("p")]}

    if kind == "object":
        rows = run_cypher(
            """
            MATCH (n:Object)
            WHERE n.id = $qualified OR n.id = $id
               OR (n.local_id = $id AND ($video_id = '' OR n.video_id = $video_id))
               OR ($id <> '' AND toLower(n.label) = toLower($id) AND ($video_id = '' OR n.video_id = $video_id))
            RETURN n
            """,
            {"id": node_id, "qualified": qualified, "video_id": video_id},
            limit=10,
        )
        return {"rows": [row["n"] for row in rows if row.get("n")]}

    rows = run_cypher(
        f"""
        MATCH (n:{label})
        WHERE n.id = $qualified OR n.id = $id
           OR (n.local_id = $id AND ($video_id = '' OR n.video_id = $video_id))
        RETURN n
        """,
        {"id": node_id, "qualified": qualified, "video_id": video_id},
        limit=10,
    )
    return {"rows": [row["n"] for row in rows if row.get("n")]}


def _write(kind: str, before: dict, patch: dict, user_request: str) -> dict:
    node_id = before["id"]
    label = _LABEL[kind]
    props = {key: value for key, value in patch.items() if not key.startswith("_")}
    props["correction_note"] = user_request
    props["corrected_at"] = datetime.now(timezone.utc).isoformat()
    assignments = ", ".join(f"n.{key} = ${key}" for key in props)
    result = run_write(
        f"MATCH (n:{label} {{id: $node_id}}) SET {assignments}",
        {"node_id": node_id, **props},
    )
    if kind == "event" and "type" in patch and patch["type"] != before.get("type"):
        run_write(
            """
            MATCH (e:Event {id: $node_id})-[old:INSTANCE_OF]->(:EventType)
            DELETE old
            WITH e
            MERGE (t:EventType {id: $type_id})
            ON CREATE SET t.title = $title, t.name = $title
            MERGE (e)-[:INSTANCE_OF]->(t)
            WITH e
            MATCH (c:Clip)-[:CONTAINS]->(e)
            MERGE (c)-[:HAS_TYPE]->(t)
            """,
            {
                "node_id": node_id,
                "type_id": patch["type"],
                "title": patch.get("title") or patch["type"].replace("_", " "),
            },
        )
    if patch.get("_involves_role"):
        person_id = patch.get("_involves_person") or ""
        video_id = before.get("video_id") or ""
        qualified = person_id
        if video_id and person_id and ":" not in person_id:
            qualified = f"{video_id}:{person_id}"
        event_id = node_id if kind == "event" else ""
        if not event_id:
            return {**result, "error": "involves_role requires kind=event"}
        if not person_id:
            return {**result, "error": "involves_person is required when involves_role is set"}
        rel = run_write(
            """
            MATCH (e:Event {id: $event_id})
            MATCH (p:Person)
            WHERE p.id = $qualified OR p.id = $person_id OR p.local_id = $person_id
            MERGE (e)-[r:INVOLVES]->(p)
            SET r.role = $role
            """,
            {
                "event_id": event_id,
                "qualified": qualified,
                "person_id": person_id,
                "role": patch["_involves_role"],
            },
        )
        result["involves"] = rel
        if patch["_involves_role"] == "potential_suspect":
            run_write(
                """
                MATCH (e:Event {id: $event_id})
                MATCH (p:Person)
                WHERE p.id = $qualified OR p.local_id = $person_id
                SET p.potential_suspect = true,
                    p.role = CASE WHEN p.role = 'officer' THEN p.role ELSE 'potential_suspect' END
                WITH e, p
                SET e.subject_ids = CASE
                  WHEN e.subject_ids IS NULL THEN [p.local_id]
                  WHEN p.local_id IN e.subject_ids THEN e.subject_ids
                  ELSE e.subject_ids + p.local_id
                END
                """,
                {
                    "event_id": event_id,
                    "qualified": qualified,
                    "person_id": person_id,
                },
            )
    return result


def _public(kind: str, node: dict) -> dict:
    keys = ["id", "video_id", "local_id"]
    keys.extend(sorted(ALLOWED[kind]))
    if kind == "event":
        keys.extend(["start_timestamp", "end_timestamp", "seek_s", "type", "summary"])
    if kind == "person":
        keys.extend(["is_cop", "role", "potential_suspect", "clothes", "description"])
    out = {}
    seen = set()
    for key in keys:
        if key in node and key not in seen:
            out[key] = node[key]
            seen.add(key)
    return out
