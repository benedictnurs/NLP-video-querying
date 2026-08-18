from __future__ import annotations

from datetime import datetime, timezone

from graph_mcp.db import run_cypher, run_write, run_writes
from workers.clock import format_clock, parse_clock

KINDS = ("person", "vehicle", "event", "object", "plate", "clip")
CREATE_KINDS = ("person", "vehicle", "event", "object", "plate")
ACTIONS = ("update", "create", "link", "merge")
MERGE_KINDS = ("person", "vehicle", "object", "plate")
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
    "video": "Video",
    "event_type": "EventType",
}

_VIDEO_REL = {
    "person": "HAS_PERSON",
    "vehicle": "HAS_VEHICLE",
    "object": "HAS_OBJECT",
    "plate": "HAS_PLATE",
}

RELS = {
    "INVOLVES": {("event", "person"), ("event", "vehicle"), ("event", "object")},
    "CONTAINS": {("clip", "person"), ("clip", "vehicle"), ("clip", "object"), ("clip", "plate"), ("clip", "event")},
    "HAS_PERSON": {("video", "person")},
    "HAS_VEHICLE": {("video", "vehicle")},
    "HAS_OBJECT": {("video", "object")},
    "HAS_PLATE": {("video", "plate"), ("vehicle", "plate")},
    "CONTINUES": {("event", "event")},
    "INSTANCE_OF": {("event", "event_type")},
}


def apply_correction(
    *,
    user_request: str,
    kind: str,
    confirmed: bool,
    action: str = "update",
    node_id: str = "",
    video_id: str = "",
    clip: str = "",
    start_timestamp: str = "",
    event_type: str = "",
    involves_person: str = "",
    involves_role: str = "",
    involves_vehicle: str = "",
    involves_object: str = "",
    continues_from: str = "",
    changes: dict | None = None,
) -> dict:
    request = (user_request or "").strip()
    if len(request) < 8:
        return {
            "error": "user_request is required. Quote the user's ask or correction. Do not invent a reason.",
        }
    action_key = (action or "update").strip().lower()
    if action_key not in ACTIONS:
        return {"error": f"action must be one of {list(ACTIONS)}"}
    if action_key == "link":
        return {
            "error": "Use link_graph to create relationships.",
            "rel_types": sorted(RELS),
        }
    if action_key == "merge":
        return {
            "error": "Use merge_graph to dedupe nodes. Pass keep_id (canonical) and drop_id (duplicate).",
        }
    kind_key = (kind or "").strip().lower()
    if action_key != "link" and kind_key not in KINDS:
        return {"error": f"kind must be one of {list(KINDS)}", "kind": kind}

    patch = {key: value for key, value in (changes or {}).items() if value is not None and value != ""}
    if involves_role.strip():
        patch["_involves_role"] = involves_role.strip()
        patch["_involves_person"] = involves_person.strip()
    if involves_vehicle.strip():
        patch["_involves_vehicle"] = involves_vehicle.strip()
    if involves_object.strip():
        patch["_involves_object"] = involves_object.strip()
    if continues_from.strip():
        patch["_continues_from"] = continues_from.strip()
    if clip.strip():
        patch["_clip"] = clip.strip()

    if action_key == "create":
        return _create(
            request=request,
            kind=kind_key,
            confirmed=confirmed,
            node_id=node_id.strip(),
            video_id=video_id.strip(),
            clip=clip.strip(),
            start_timestamp=start_timestamp.strip(),
            event_type=event_type.strip(),
            patch=patch,
        )

    if not patch:
        return {
            "error": "No fields to change.",
            "allowed": sorted(ALLOWED.get(kind_key) or []),
            "hint": "Pass clothes, role, is_cop, type, start_timestamp, plate, or action=create / link_graph.",
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
    )
    if found.get("error"):
        return found
    rows = found["rows"]
    if not rows:
        return {
            "error": "No matching node. Pass id and video_id, or action=create to add one.",
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
        "action": "update",
        "user_request": request,
        "kind": kind_key,
        "id": before.get("id"),
        "before": _public(kind_key, before),
        "set": props,
        "wrote": False,
    }
    _preview_links(preview, cleaned, involves_person, involves_role)
    if not confirmed:
        preview["hint"] = _preview_hint()
        return preview

    written = _write(kind_key, before, cleaned, request)
    after = _find(
        kind_key,
        node_id=before.get("id") or node_id,
        video_id=before.get("video_id") or video_id,
        start_timestamp=cleaned.get("start_timestamp") or start_timestamp,
        event_type=cleaned.get("type") or event_type,
    )
    preview["wrote"] = True
    preview["write"] = written
    preview["after"] = _public(kind_key, (after.get("rows") or [before])[0])
    return preview


def apply_link(
    *,
    user_request: str,
    confirmed: bool,
    rel: str,
    from_kind: str,
    from_id: str,
    to_kind: str,
    to_id: str,
    video_id: str = "",
    role: str = "",
) -> dict:
    request = (user_request or "").strip()
    if len(request) < 8:
        return {
            "error": "user_request is required. Quote the user's ask or correction. Do not invent a reason.",
        }
    rel_key = (rel or "").strip().upper().replace("-", "_")
    left = (from_kind or "").strip().lower()
    right = (to_kind or "").strip().lower()
    if rel_key not in RELS:
        return {"error": f"rel must be one of {sorted(RELS)}"}
    if (left, right) not in RELS[rel_key]:
        allowed = [f"{a}->{b}" for a, b in sorted(RELS[rel_key])]
        return {"error": f"{rel_key} cannot connect {left} to {right}.", "allowed": allowed}
    if not from_id.strip() or not to_id.strip():
        return {"error": "from_id and to_id are required."}
    role_key = (role or "").strip()
    if rel_key == "INVOLVES" and right == "person":
        if role_key and role_key not in ROLES:
            return {"error": f"role must be one of {list(ROLES)}"}
        role_key = role_key or "unknown"

    left_node = _locate(left, from_id.strip(), video_id.strip())
    right_node = _locate(right, to_id.strip(), video_id.strip())
    if left_node.get("error"):
        return left_node
    if right_node.get("error"):
        return right_node

    preview = {
        "action": "link",
        "user_request": request,
        "rel": rel_key,
        "from": _public(left, left_node["row"]) if left in ALLOWED else {"id": left_node["row"].get("id")},
        "to": _public(right, right_node["row"]) if right in ALLOWED else {"id": right_node["row"].get("id")},
        "role": role_key or None,
        "wrote": False,
    }
    if not confirmed:
        preview["hint"] = _preview_hint()
        return preview

    result = _merge_rel(
        rel_key,
        left,
        left_node["row"]["id"],
        right,
        right_node["row"]["id"],
        role=role_key,
        note=request,
    )
    preview["wrote"] = True
    preview["write"] = result
    return preview


def apply_merge(
    *,
    user_request: str,
    confirmed: bool,
    kind: str,
    keep_id: str,
    drop_id: str,
    video_id: str = "",
    changes: dict | None = None,
) -> dict:
    request = (user_request or "").strip()
    if len(request) < 8:
        return {
            "error": "user_request is required. Quote the user's ask or correction. Do not invent a reason.",
        }
    kind_key = (kind or "person").strip().lower()
    if kind_key not in MERGE_KINDS:
        return {"error": f"Can only merge {list(MERGE_KINDS)}. Not Video, Clip, or Event."}
    if not keep_id.strip() or not drop_id.strip():
        return {"error": "keep_id (canonical) and drop_id (duplicate) are required."}

    keep_node = _locate(kind_key, keep_id.strip(), video_id.strip())
    drop_node = _locate(kind_key, drop_id.strip(), video_id.strip())
    if keep_node.get("error"):
        return keep_node
    if drop_node.get("error"):
        return drop_node
    keep = keep_node["row"]
    drop = drop_node["row"]
    if keep.get("id") == drop.get("id"):
        return {"error": "keep_id and drop_id are the same node."}
    keep_video = keep.get("video_id") or ""
    drop_video = drop.get("video_id") or ""
    if keep_video and drop_video and keep_video != drop_video:
        return {
            "error": "Refusing to merge identities across videos. Keep them separate unless they are the same person in the same file.",
            "keep_video": keep_video,
            "drop_video": drop_video,
        }

    patch = {key: value for key, value in (changes or {}).items() if value is not None and value != ""}
    cleaned = {}
    if patch:
        cleaned, err = _clean_patch(kind_key, patch)
        if err:
            return err
        cleaned = {key: value for key, value in cleaned.items() if not key.startswith("_")}

    filled = _fill_keep(kind_key, keep, drop, cleaned)
    rels = _identity_rels(kind_key, drop["id"])
    preview = {
        "action": "merge",
        "user_request": request,
        "kind": kind_key,
        "keep": _public(kind_key, keep),
        "drop": _public(kind_key, drop),
        "set": filled,
        "move_relationships": rels,
        "wrote": False,
    }
    if not confirmed:
        preview["hint"] = (
            "Preview only. keep_id stays. drop_id is deleted after its clips/events/plates "
            "are rewired onto keep. Set confirmed=true ONLY if the user said these are the same identity."
        )
        return preview

    written = _merge_identity(kind_key, keep, drop, filled, request)
    after = _locate(kind_key, keep["id"], keep_video or video_id)
    gone = _locate(kind_key, drop["id"], drop_video or video_id)
    preview["wrote"] = True
    preview["write"] = written
    preview["after"] = _public(kind_key, after["row"]) if not after.get("error") else after
    preview["dropped"] = gone.get("error") or "duplicate still present"
    return preview


def _fill_keep(kind: str, keep: dict, drop: dict, overrides: dict) -> dict:
    filled = dict(overrides)
    for key in ALLOWED.get(kind) or []:
        if key in filled:
            continue
        k = keep.get(key)
        d = drop.get(key)
        if key == "potential_suspect":
            filled[key] = bool(k) or bool(d)
            continue
        if key == "is_cop":
            filled[key] = k if k is not None else d
            continue
        if key == "role":
            filled[key] = _prefer_role(k, d)
            continue
        if _blank(k) and not _blank(d):
            filled[key] = d
    aliases = []
    for value in keep.get("also_known_as") or []:
        if value and value not in aliases:
            aliases.append(value)
    for value in drop.get("also_known_as") or []:
        if value and value not in aliases:
            aliases.append(value)
    drop_local = drop.get("local_id") or drop.get("id")
    if drop_local and drop_local not in aliases and drop_local != keep.get("local_id"):
        aliases.append(drop_local)
    filled["also_known_as"] = aliases
    merged = list(keep.get("merged_ids") or [])
    if drop.get("id") and drop["id"] not in merged:
        merged.append(drop["id"])
    filled["merged_ids"] = merged
    return filled


def _prefer_role(keep, drop) -> str:
    rank = {"potential_suspect": 3, "officer": 2, "civilian": 1, "unknown": 0, "": 0, None: 0}
    keep_s = keep or "unknown"
    drop_s = drop or "unknown"
    return keep_s if rank.get(keep_s, 0) >= rank.get(drop_s, 0) else drop_s


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in {"", "unknown"}:
        return True
    return False


def _identity_rels(kind: str, node_id: str) -> dict:
    video_rel = _VIDEO_REL[kind]
    rows = run_cypher(
        f"""
        MATCH (drop:{_LABEL[kind]} {{id: $id}})
        OPTIONAL MATCH (e:Event)-[ie:INVOLVES]->(drop)
        OPTIONAL MATCH (c:Clip)-[:CONTAINS]->(drop)
        OPTIONAL MATCH (v:Video)-[:{video_rel}]->(drop)
        OPTIONAL MATCH (drop)-[:HAS_PLATE]->(pl:Plate)
        RETURN collect(DISTINCT {{event: e.id, type: e.type, clock: e.start_timestamp, role: ie.role}}) AS events,
               collect(DISTINCT c.local_id) AS clips,
               collect(DISTINCT v.id) AS videos,
               collect(DISTINCT pl.text) AS plates
        """,
        {"id": node_id},
        limit=1,
    )
    row = rows[0] if rows else {}
    events = [item for item in (row.get("events") or []) if item.get("event")]
    clips = [item for item in (row.get("clips") or []) if item]
    videos = [item for item in (row.get("videos") or []) if item]
    plates = [item for item in (row.get("plates") or []) if item]
    return {"events": events, "clips": clips, "videos": videos, "plates": plates}


def _merge_identity(kind: str, keep: dict, drop: dict, filled: dict, request: str) -> dict:
    label = _LABEL[kind]
    video_rel = _VIDEO_REL[kind]
    keep_id = keep["id"]
    drop_id = drop["id"]
    keep_local = keep.get("local_id") or ""
    drop_local = drop.get("local_id") or ""
    filled = {
        **filled,
        "correction_note": request,
        "corrected_at": datetime.now(timezone.utc).isoformat(),
    }
    assignments = ", ".join(f"keep.{key} = ${key}" for key in filled)
    statements = [
        (
            f"""
            MATCH (keep:{label} {{id: $keep_id}})
            MATCH (drop:{label} {{id: $drop_id}})
            MATCH (e:Event)-[r:INVOLVES]->(drop)
            MERGE (e)-[r2:INVOLVES]->(keep)
            SET r2.role = CASE
              WHEN r2.role = 'potential_suspect' OR r.role = 'potential_suspect' THEN 'potential_suspect'
              WHEN r2.role = 'officer' OR r.role = 'officer' THEN 'officer'
              WHEN r2.role IS NOT NULL AND r2.role <> 'unknown' THEN r2.role
              ELSE r.role
            END
            DELETE r
            """,
            {"keep_id": keep_id, "drop_id": drop_id},
        ),
        (
            f"""
            MATCH (keep:{label} {{id: $keep_id}})
            MATCH (c:Clip)-[r:CONTAINS]->(drop:{label} {{id: $drop_id}})
            MERGE (c)-[:CONTAINS]->(keep)
            DELETE r
            """,
            {"keep_id": keep_id, "drop_id": drop_id},
        ),
        (
            f"""
            MATCH (keep:{label} {{id: $keep_id}})
            MATCH (v:Video)-[r:{video_rel}]->(drop:{label} {{id: $drop_id}})
            MERGE (v)-[:{video_rel}]->(keep)
            DELETE r
            """,
            {"keep_id": keep_id, "drop_id": drop_id},
        ),
    ]
    if kind == "vehicle":
        statements.append(
            (
                """
                MATCH (keep:Vehicle {id: $keep_id})
                MATCH (drop:Vehicle {id: $drop_id})-[r:HAS_PLATE]->(p:Plate)
                MERGE (keep)-[:HAS_PLATE]->(p)
                DELETE r
                """,
                {"keep_id": keep_id, "drop_id": drop_id},
            )
        )
    if kind == "person" and drop_local:
        statements.append(
            (
                """
                MATCH (e:Event)
                WHERE $drop_local IN coalesce(e.subject_ids, [])
                SET e.subject_ids = [x IN coalesce(e.subject_ids, []) WHERE x <> $drop_local]
                  + CASE WHEN $keep_local IN coalesce(e.subject_ids, []) OR $keep_local = '' THEN [] ELSE [$keep_local] END
                """,
                {"drop_local": drop_local, "keep_local": keep_local},
            )
        )
    statements.append(
        (
            f"""
            MATCH (keep:{label} {{id: $keep_id}})
            SET {assignments}
            """,
            {"keep_id": keep_id, **filled},
        )
    )
    statements.append(
        (
            f"MATCH (drop:{label} {{id: $drop_id}}) DETACH DELETE drop",
            {"drop_id": drop_id},
        )
    )
    return run_writes(statements)


def _create(
    *,
    request: str,
    kind: str,
    confirmed: bool,
    node_id: str,
    video_id: str,
    clip: str,
    start_timestamp: str,
    event_type: str,
    patch: dict,
) -> dict:
    if kind not in CREATE_KINDS:
        return {"error": f"Cannot create {kind}. Create person, vehicle, event, object, or plate."}
    if not video_id:
        return {"error": "video_id is required to create a node."}
    video = _locate("video", video_id, video_id)
    if video.get("error"):
        return {"error": f"Video {video_id} is not in the graph. Ingest it first."}

    if kind == "event":
        if "type" not in patch and event_type:
            patch["type"] = event_type
        if "start_timestamp" not in patch and start_timestamp:
            patch["start_timestamp"] = start_timestamp
    if kind == "plate" and "text" not in patch and node_id:
        patch["text"] = node_id

    cleaned, err = _clean_patch(kind, patch) if any(
        key for key in patch if not str(key).startswith("_")
    ) else ({}, None)
    if err:
        return err
    for key, value in patch.items():
        if key.startswith("_"):
            cleaned[key] = value

    clip_row = None
    clip_ref = clip or cleaned.get("_clip") or ""
    if clip_ref or (kind == "event" and start_timestamp):
        clip_row = _resolve_clip(video_id, clip_ref, start_timestamp or cleaned.get("start_timestamp") or "")
        if clip_row.get("error"):
            return clip_row
        clip_row = clip_row.get("row")
    if kind == "event" and not clip_row:
        return {"error": "Creating an event needs clip=clip_0009 (or a start_timestamp that falls in a clip)."}
    if kind == "event" and not cleaned.get("type"):
        return {"error": "Creating an event needs event_type or new_type (catalog id)."}

    if node_id or (kind == "event" and cleaned.get("type") and cleaned.get("start_timestamp")):
        found = _find(
            kind,
            node_id=node_id,
            video_id=video_id,
            start_timestamp=cleaned.get("start_timestamp") or start_timestamp,
            event_type=cleaned.get("type") or event_type,
        )
        if found.get("error") and "Pass id" not in (found.get("error") or ""):
            return found
        if found.get("rows"):
            return {
                "error": "That node already exists. Use action=update, or link_graph to add a relation.",
                "existing": _public(kind, found["rows"][0]),
            }

    local_id, graph_id = _new_ids(kind, node_id, video_id, clip_row, cleaned)
    props = {key: value for key, value in cleaned.items() if not key.startswith("_")}
    props.update(_create_defaults(kind, local_id, video_id, graph_id, clip_row, cleaned))
    props["correction_note"] = request
    preview = {
        "action": "create",
        "user_request": request,
        "kind": kind,
        "id": graph_id,
        "local_id": local_id,
        "video_id": video_id,
        "clip": (clip_row or {}).get("local_id") or clip_ref,
        "set": {key: props[key] for key in props if key not in {"correction_note", "corrected_at", "source"}},
        "links": _create_link_preview(kind, graph_id, video_id, clip_row, cleaned),
        "wrote": False,
    }
    if not confirmed:
        preview["hint"] = _preview_hint()
        return preview

    written = _merge_node(kind, graph_id, props)
    attached = _attach(kind, graph_id, video_id, clip_row, cleaned, request)
    after = _find(kind, node_id=graph_id, video_id=video_id, start_timestamp="", event_type="")
    preview["wrote"] = True
    preview["write"] = {"node": written, "links": attached}
    preview["after"] = _public(kind, (after.get("rows") or [props])[0])
    return preview


def _create_defaults(kind: str, local_id: str, video_id: str, graph_id: str, clip_row: dict | None, cleaned: dict) -> dict:
    stamp = {
        "id": graph_id,
        "video_id": video_id,
        "local_id": local_id,
        "source": "user",
    }
    if kind == "person":
        stamp.setdefault("race", cleaned.get("race") or "unknown")
        stamp.setdefault("gender", cleaned.get("gender") or "unknown")
        stamp.setdefault("role", cleaned.get("role") or "civilian")
        stamp.setdefault("potential_suspect", bool(cleaned.get("potential_suspect")))
    if kind == "event":
        stamp["definition"] = cleaned.get("type")
        stamp["title"] = cleaned.get("title") or str(cleaned.get("type") or "").replace("_", " ")
        if clip_row:
            stamp["end_timestamp"] = cleaned.get("end_timestamp") or clip_row.get("end_timestamp")
    if kind == "plate":
        stamp["text"] = cleaned.get("text") or local_id
    return stamp


def _create_link_preview(kind: str, graph_id: str, video_id: str, clip_row: dict | None, cleaned: dict) -> list[str]:
    links = []
    if kind in _VIDEO_REL:
        links.append(f"(Video {video_id})-[:{_VIDEO_REL[kind]}]->({kind} {graph_id})")
    if clip_row:
        links.append(f"(Clip {clip_row.get('id')})-[:CONTAINS]->({kind} {graph_id})")
    if kind == "event":
        links.append(f"(Event)-[:INSTANCE_OF]->(EventType {cleaned.get('type')})")
        if cleaned.get("_involves_person"):
            links.append(
                f"(Event)-[:INVOLVES {{role:{cleaned.get('_involves_role') or 'unknown'}}}]->(Person {cleaned['_involves_person']})"
            )
        if cleaned.get("_involves_vehicle"):
            links.append(f"(Event)-[:INVOLVES]->(Vehicle {cleaned['_involves_vehicle']})")
        if cleaned.get("_involves_object"):
            links.append(f"(Event)-[:INVOLVES]->(Object {cleaned['_involves_object']})")
        if cleaned.get("_continues_from"):
            links.append(f"(Event {cleaned['_continues_from']})-[:CONTINUES]->(Event)")
    if kind == "vehicle" and cleaned.get("plate"):
        links.append(f"(Vehicle)-[:HAS_PLATE]->(Plate {cleaned['plate']})")
    return links


def _new_ids(kind: str, node_id: str, video_id: str, clip_row: dict | None, cleaned: dict) -> tuple[str, str]:
    if kind == "event":
        type_id = cleaned["type"]
        start_ms = parse_clock(cleaned["start_timestamp"]) or 0
        clip_id = (clip_row or {}).get("id") or f"{video_id}:clip"
        graph_id = f"{clip_id}:{type_id}:{start_ms}"
        return type_id, graph_id
    if kind == "plate":
        text = str(cleaned.get("text") or node_id).strip().upper()
        return text, f"{video_id}:plate:{text}"
    local = node_id
    if local.startswith(f"{video_id}:"):
        local = local.split(":", 1)[1]
    if not local:
        local = _next_local(kind, video_id)
    return local, f"{video_id}:{local}"


def _next_local(kind: str, video_id: str) -> str:
    prefix = {"person": "person_", "vehicle": "vehicle_", "object": "object_"}[kind]
    rows = run_cypher(
        f"""
        MATCH (n:{_LABEL[kind]} {{video_id: $video_id}})
        RETURN n.local_id AS local_id, n.id AS id
        """,
        {"video_id": video_id},
        limit=200,
    )
    best = 0
    for row in rows:
        text = str(row.get("local_id") or "")
        if text.startswith(prefix) and text[len(prefix) :].isdigit():
            best = max(best, int(text[len(prefix) :]))
            continue
        ident = str(row.get("id") or "")
        tail = ident.rsplit(":", 1)[-1]
        if tail.startswith(prefix) and tail[len(prefix) :].isdigit():
            best = max(best, int(tail[len(prefix) :]))
    return f"{prefix}{best + 1}"


def _merge_node(kind: str, graph_id: str, props: dict) -> dict:
    props = {
        **props,
        "correction_note": props.get("correction_note") or "",
        "corrected_at": datetime.now(timezone.utc).isoformat(),
        "source": props.get("source") or "user",
    }
    assignments = ", ".join(f"n.{key} = ${key}" for key in props if key != "id")
    return run_write(
        f"MERGE (n:{_LABEL[kind]} {{id: $id}}) SET {assignments}",
        props,
    )


def _attach(kind: str, graph_id: str, video_id: str, clip_row: dict | None, cleaned: dict, request: str) -> dict:
    out = {}
    video_rel = _VIDEO_REL.get(kind)
    if video_rel:
        out["video"] = _merge_rel(video_rel, "video", video_id, kind, graph_id, note=request)
    if clip_row:
        out["clip"] = _merge_rel("CONTAINS", "clip", clip_row["id"], kind, graph_id, note=request)
    if kind == "event":
        type_id = cleaned.get("type")
        out["type"] = run_write(
            """
            MATCH (e:Event {id: $event_id})
            MERGE (t:EventType {id: $type_id})
            ON CREATE SET t.title = $title, t.name = $title
            MERGE (e)-[:INSTANCE_OF]->(t)
            WITH e, t
            MATCH (c:Clip)-[:CONTAINS]->(e)
            MERGE (c)-[:HAS_TYPE]->(t)
            WITH t
            MATCH (v:Video {id: $video_id})
            MERGE (v)-[:HAS_TYPE]->(t)
            """,
            {
                "event_id": graph_id,
                "type_id": type_id,
                "title": cleaned.get("title") or str(type_id or "").replace("_", " "),
                "video_id": video_id,
            },
        )
        out["involves"] = _write_involves(
            graph_id,
            video_id,
            cleaned,
            request,
        )
        if cleaned.get("_continues_from"):
            prev = _locate("event", cleaned["_continues_from"], video_id)
            if not prev.get("error"):
                out["continues"] = _merge_rel(
                    "CONTINUES",
                    "event",
                    prev["row"]["id"],
                    "event",
                    graph_id,
                    note=request,
                )
    if kind == "vehicle" and cleaned.get("plate"):
        plate_id = f"{video_id}:plate:{cleaned['plate']}"
        run_write(
            """
            MERGE (p:Plate {id: $plate_id})
            SET p.text = $text, p.video_id = $video_id, p.source = 'user'
            """,
            {"plate_id": plate_id, "text": cleaned["plate"], "video_id": video_id},
        )
        out["plate"] = _merge_rel("HAS_PLATE", "vehicle", graph_id, "plate", plate_id, note=request)
        _merge_rel("HAS_PLATE", "video", video_id, "plate", plate_id, note=request)
        if clip_row:
            _merge_rel("CONTAINS", "clip", clip_row["id"], "plate", plate_id, note=request)
    return out


def _write_involves(event_id: str, video_id: str, cleaned: dict, request: str) -> dict:
    out = {}
    person = cleaned.get("_involves_person") or ""
    if person:
        located = _locate("person", person, video_id)
        if not located.get("error"):
            role = cleaned.get("_involves_role") or "unknown"
            out["person"] = _merge_rel(
                "INVOLVES",
                "event",
                event_id,
                "person",
                located["row"]["id"],
                role=role,
                note=request,
            )
            if role == "potential_suspect":
                run_write(
                    """
                    MATCH (e:Event {id: $event_id})
                    MATCH (p:Person {id: $person_id})
                    SET p.potential_suspect = true,
                        p.role = CASE WHEN p.role = 'officer' THEN p.role ELSE 'potential_suspect' END,
                        e.subject_ids = CASE
                          WHEN e.subject_ids IS NULL THEN [p.local_id]
                          WHEN p.local_id IN e.subject_ids THEN e.subject_ids
                          ELSE e.subject_ids + p.local_id
                        END
                    """,
                    {"event_id": event_id, "person_id": located["row"]["id"]},
                )
    vehicle = cleaned.get("_involves_vehicle") or ""
    if vehicle:
        located = _locate("vehicle", vehicle, video_id)
        if not located.get("error"):
            out["vehicle"] = _merge_rel(
                "INVOLVES", "event", event_id, "vehicle", located["row"]["id"], note=request
            )
    obj = cleaned.get("_involves_object") or ""
    if obj:
        located = _locate("object", obj, video_id)
        if not located.get("error"):
            out["object"] = _merge_rel(
                "INVOLVES", "event", event_id, "object", located["row"]["id"], note=request
            )
    return out


def _merge_rel(
    rel: str,
    from_kind: str,
    from_id: str,
    to_kind: str,
    to_id: str,
    role: str = "",
    note: str = "",
) -> dict:
    left = _LABEL[from_kind]
    right = _LABEL[to_kind]
    set_bits = ["r.correction_note = $note", "r.corrected_at = $at"]
    params = {
        "from_id": from_id,
        "to_id": to_id,
        "note": note,
        "at": datetime.now(timezone.utc).isoformat(),
        "role": role or None,
    }
    if rel == "INVOLVES" and role:
        set_bits.append("r.role = $role")
    return run_write(
        f"""
        MATCH (a:{left} {{id: $from_id}})
        MATCH (b:{right} {{id: $to_id}})
        MERGE (a)-[r:{rel}]->(b)
        SET {", ".join(set_bits)}
        """,
        params,
    )


def _locate(kind: str, node_id: str, video_id: str) -> dict:
    if kind == "video":
        rows = run_cypher(
            "MATCH (v:Video {id: $id}) RETURN v",
            {"id": node_id or video_id},
            limit=2,
        )
        nodes = [row["v"] for row in rows if row.get("v")]
        if not nodes:
            return {"error": f"No Video {node_id or video_id}"}
        return {"row": nodes[0]}
    if kind == "event_type":
        rows = run_cypher(
            "MATCH (t:EventType {id: $id}) RETURN t",
            {"id": node_id},
            limit=2,
        )
        nodes = [row["t"] for row in rows if row.get("t")]
        if not nodes:
            return {"error": f"No EventType {node_id}"}
        return {"row": nodes[0]}
    found = _find(kind, node_id=node_id, video_id=video_id, start_timestamp="", event_type="")
    if found.get("error"):
        return found
    rows = found.get("rows") or []
    if not rows:
        return {"error": f"No {kind} matched {node_id}"}
    if len(rows) > 1:
        return {"error": f"Multiple {kind} matches for {node_id}. Pass video_id.", "matches": rows[:5]}
    return {"row": rows[0]}


def _resolve_clip(video_id: str, clip: str, start_timestamp: str) -> dict:
    if clip:
        found = _find("clip", node_id=clip, video_id=video_id, start_timestamp="", event_type="")
        if found.get("error"):
            return found
        if not found.get("rows"):
            return {"error": f"No clip {clip} on {video_id}"}
        if len(found["rows"]) > 1:
            return {"error": "Multiple clips matched. Pass video_id.", "matches": found["rows"][:5]}
        return {"row": found["rows"][0]}
    ms = parse_clock(start_timestamp)
    if ms is None:
        return {"error": "Pass clip=clip_0009 or a start_timestamp in a clip."}
    seek_s = ms / 1000.0
    rows = run_cypher(
        """
        MATCH (c:Clip)
        WHERE $video_id = '' OR c.video_id = $video_id
        RETURN c
        """,
        {"video_id": video_id},
        limit=80,
    )
    hits = []
    for row in rows:
        node = row.get("c") or {}
        start = parse_clock(node.get("start_timestamp"))
        end = parse_clock(node.get("end_timestamp"))
        if start is None or end is None:
            continue
        if start / 1000.0 <= seek_s <= end / 1000.0:
            hits.append(node)
    if not hits:
        return {"error": f"No clip covers {start_timestamp} on {video_id}"}
    return {"row": hits[0]}


def _preview_links(preview: dict, cleaned: dict, involves_person: str, involves_role: str) -> None:
    if "_involves_role" in cleaned:
        preview["involves"] = {
            "person": cleaned.get("_involves_person") or involves_person,
            "role": cleaned["_involves_role"],
        }
    if cleaned.get("_involves_vehicle"):
        preview["involves_vehicle"] = cleaned["_involves_vehicle"]
    if cleaned.get("_involves_object"):
        preview["involves_object"] = cleaned["_involves_object"]
    if cleaned.get("_continues_from"):
        preview["continues_from"] = cleaned["_continues_from"]


def _preview_hint() -> str:
    return (
        "Preview only. Set confirmed=true ONLY if the user asked to change the graph "
        "or corrected this fact. Do not write after analyzing a scene on your own."
    )


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
    for extra in ("_involves_vehicle", "_involves_object", "_continues_from", "_clip"):
        if patch.get(extra):
            cleaned[extra] = patch[extra]
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
    video_id = before.get("video_id") or ""
    clip_row = None
    if patch.get("_clip"):
        resolved = _resolve_clip(video_id, patch["_clip"], "")
        if not resolved.get("error"):
            clip_row = resolved["row"]
            _merge_rel("CONTAINS", "clip", clip_row["id"], kind, node_id, note=user_request)
            video_rel = _VIDEO_REL.get(kind)
            if video_rel and video_id:
                _merge_rel(video_rel, "video", video_id, kind, node_id, note=user_request)
    if kind == "event":
        result["involves"] = _write_involves(node_id, video_id, patch, user_request)
        if patch.get("_continues_from"):
            prev = _locate("event", patch["_continues_from"], video_id)
            if not prev.get("error"):
                result["continues"] = _merge_rel(
                    "CONTINUES",
                    "event",
                    prev["row"]["id"],
                    "event",
                    node_id,
                    note=user_request,
                )
    return result


def _public(kind: str, node: dict) -> dict:
    keys = ["id", "video_id", "local_id"]
    keys.extend(sorted(ALLOWED.get(kind) or []))
    if kind == "event":
        keys.extend(["start_timestamp", "end_timestamp", "seek_s", "type", "summary"])
    if kind == "person":
        keys.extend(
            ["is_cop", "role", "potential_suspect", "clothes", "description", "also_known_as", "merged_ids"]
        )
    out = {}
    seen = set()
    for key in keys:
        if key in node and key not in seen:
            out[key] = node[key]
            seen.add(key)
    return out
