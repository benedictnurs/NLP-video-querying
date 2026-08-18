from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastmcp import FastMCP

from fastmcp.utilities.types import Image

from graph_mcp.blocks import assemble, catalog, parse_pipeline, public_params, recipe_steps
from graph_mcp.db import dumps, load_env, run_cypher
from graph_mcp.queries import (
    clock_seconds,
    clip_at_timestamp as q_clip_at_timestamp,
    clip_attachments as q_clip_attachments,
    count_events as q_count_events,
    event_at_time as q_event_at_time,
    event_chain as q_event_chain,
    event_participants as q_event_participants,
    events_by_type as q_events_by_type,
    events_by_types as q_events_by_types,
    events_in_timeframe as q_events_in_timeframe,
    find_objects as q_find_objects,
    find_people as q_find_people,
    find_plates as q_find_plates,
    find_potential_suspects as q_find_potential_suspects,
    find_vehicles as q_find_vehicles,
    inventory as q_inventory,
    list_videos as q_list_videos,
    object_appearances as q_object_appearances,
    person_timeline as q_person_timeline,
    search_transcript as q_search_transcript,
)
from graph_mcp.schema import EVENT_TYPES, PRIMER
from graph_mcp.media import (
    allowed_path,
    clip_image_paths,
    reveal_in_finder,
    rewrite_graph_uris,
    to_local_path,
)
from workers.events import resolve_event_query

load_env()

mcp = FastMCP(
    name="code-four",
    instructions=(
        "Police bodycam graph for investigative search. "
        "ALWAYS call explain_graph_context first in a session before any other tool. "
        "Compose queries from blocks: list_query_blocks, then preview_blocks or run_blocks. "
        "Use run_recipe for named pipelines (all_dui, all_miranda, suspects). "
        "Use analyze_scene to pull splice/frame images into Codex for visual analysis. "
        "Use open_in_finder / open_clip_attachment to reveal the same files in Finder. "
        "Use run_custom_cypher only for gaps. potential_suspect is a scene role, not a charge."
    ),
)

_PRIMER_DONE = False
_WRITE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD|FOREACH|CALL\s+\{|PERIODIC)\b",
    re.I,
)


def _require_primer() -> str | None:
    if _PRIMER_DONE:
        return None
    return dumps(
        {
            "error": "Call explain_graph_context first. It explains nodes, relationships, timestamps, and roles. Then retry this tool.",
            "next_tool": "explain_graph_context",
        }
    )


def _run(pair, limit: int = 100):
    cypher, params = pair
    return run_cypher(cypher, params, limit=limit)


def _clock(value: str, name: str) -> float:
    seconds = clock_seconds(value)
    if seconds is None:
        raise ValueError(f"{name} must look like 01:56 or 1:02:03")
    return seconds


def _pipeline_result(raw, limit: int = 100, preview: bool = False) -> str:
    try:
        steps = parse_pipeline(raw)
        cypher, params, names = assemble(steps)
    except Exception as exc:
        return dumps({"error": str(exc)})
    payload = {
        "pipeline": steps,
        "blocks": names,
        "cypher": cypher,
        "params": public_params(params),
    }
    resolved = params.get("_resolved")
    if resolved:
        payload["resolved"] = resolved
    if preview:
        return dumps(payload)
    try:
        rows = run_cypher(cypher, public_params(params), limit=limit)
    except Exception as exc:
        payload["error"] = str(exc)
        return dumps(payload)
    payload["n"] = len(rows)
    payload["rows"] = rows
    return dumps(payload)


@mcp.tool
def explain_graph_context() -> str:
    """REQUIRED FIRST. Explains Video/Clip/Event/Person/Vehicle/Object/Plate nodes, relationships, timestamps, and officer-facing use. Call this before every other graph tool in a session."""
    global _PRIMER_DONE
    _PRIMER_DONE = True
    try:
        counts = _run(q_inventory())
        videos = _run(q_list_videos())
        types = _run(q_count_events(""))
    except Exception as exc:
        counts, videos, types = [], [], [{"error": str(exc)}]
    return dumps(
        {
            "primer": PRIMER,
            "live_inventory": counts,
            "videos": videos,
            "event_counts": types,
            "event_types": EVENT_TYPES,
            "query_blocks": catalog(),
        }
    )


@mcp.tool
def list_query_blocks() -> str:
    """Catalog of composable Neo4j blocks and named recipes. Snap a source + filters + joins + output together, then run_blocks or run_recipe."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(catalog())


@mcp.tool
def preview_blocks(pipeline: str) -> str:
    """Assemble blocks into Cypher without running. pipeline is a recipe name (all_dui), a pipe string (events | topic query=dui | with_people | return_events), or a JSON list of {block, ...params}."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return _pipeline_result(pipeline, preview=True)


@mcp.tool
def run_blocks(pipeline: str, limit: int = 100) -> str:
    """Put blocks together and execute against Neo4j. Same pipeline formats as preview_blocks. Example: events | topic query=dui | timeframe start=01:00 end=20:00 | with_people | with_clip | return_events"""
    blocked = _require_primer()
    if blocked:
        return blocked
    return _pipeline_result(pipeline, limit=limit)


@mcp.tool
def run_recipe(
    name: str,
    video_id: str = "",
    start: str = "",
    end: str = "",
    query: str = "",
    person_id: str = "",
    clothes: str = "",
    label: str = "",
    event_type: str = "",
    timestamp: str = "",
    limit: int = 100,
) -> str:
    """Run a named block pipeline: all_dui, all_miranda, all_arrests, all_stops, events_in_timeframe, count_events, suspects, person_timeline, objects, list_videos. Extra args are attached to matching blocks (e.g. start/end on timeframe)."""
    blocked = _require_primer()
    if blocked:
        return blocked
    extra = {
        "video_id": video_id,
        "start": start,
        "end": end,
        "query": query,
        "person_id": person_id,
        "clothes": clothes,
        "label": label,
        "event_type": event_type,
        "timestamp": timestamp,
    }
    try:
        steps = recipe_steps(name, extra)
    except Exception as exc:
        return dumps({"error": str(exc)})
    return _pipeline_result(steps, limit=limit)


@mcp.tool
def list_videos() -> str:
    """List ingested videos in the graph."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_list_videos()))


@mcp.tool
def count_events(event_type: str = "", video_id: str = "") -> str:
    """Count Event nodes by type across all videos (or one video_id). Empty event_type counts all types. Example: miranda_warning, arrest, traffic_stop."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_count_events(event_type, video_id)))


@mcp.tool
def events_in_timeframe(
    start_timestamp: str,
    end_timestamp: str,
    event_type: str = "",
    video_id: str = "",
) -> str:
    """Events whose start clock falls in a window, across all videos unless video_id is set. Example start_timestamp=01:00 end_timestamp=05:00."""
    blocked = _require_primer()
    if blocked:
        return blocked
    start_s = _clock(start_timestamp, "start_timestamp")
    end_s = _clock(end_timestamp, "end_timestamp")
    return dumps(_run(q_events_in_timeframe(start_s, end_s, event_type, video_id)))


@mcp.tool
def events_by_type(event_type: str, video_id: str = "") -> str:
    """List every event of one catalog type across all videos, with clocks, clip, summary, and people. Use miranda_warning for all Miranda readings."""
    blocked = _require_primer()
    if blocked:
        return blocked
    if not event_type.strip():
        return dumps({"error": "event_type is required", "event_types": EVENT_TYPES})
    return dumps(_run(q_events_by_type(event_type, video_id)))


@mcp.tool
def find_events(query: str, video_id: str = "") -> str:
    """Find events across all ingested videos by definition/topic/alias, e.g. 'dui', 'miranda rights', 'traffic stop'. Not a charge. Optional video_id limits to one file."""
    blocked = _require_primer()
    if blocked:
        return blocked
    resolved = resolve_event_query(query)
    types = resolved.get("event_types") or []
    if not types:
        return dumps(
            {
                "error": "No event definition matched that query.",
                "resolved": resolved,
                "hint": "Try dui, miranda, traffic stop, arrest, or an Event.type id.",
            }
        )
    rows = _run(q_events_by_types(types, video_id))
    return dumps({"resolved": resolved, "n": len(rows), "events": rows})


@mcp.tool
def event_at_time(timestamp: str, event_type: str = "") -> str:
    """Events nearest a clock, e.g. timestamp=01:56. Optional event_type."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_event_at_time(_clock(timestamp, "timestamp"), event_type)))


@mcp.tool
def event_participants(event_type: str, start_timestamp: str) -> str:
    """People, vehicles, and objects involved in one event, with INVOLVES.role."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_event_participants(event_type, start_timestamp)))


@mcp.tool
def find_people(
    clothes: str = "",
    role: str = "",
    is_cop: bool | None = None,
    potential_suspect: bool | None = None,
) -> str:
    """Find people by clothes, role (officer|civilian|potential_suspect), uniform, or suspect flag."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_find_people(clothes, role, is_cop, potential_suspect)))


@mcp.tool
def find_potential_suspects() -> str:
    """Civilians tagged potential_suspect: snapshot, reason, first event/time, and later subject events. Not a charge."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_find_potential_suspects()))


@mcp.tool
def person_timeline(person_id: str) -> str:
    """All events involving a person local id such as person_3, ordered by start_timestamp."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_person_timeline(person_id)))


@mcp.tool
def find_vehicles(color: str = "", plate: str = "") -> str:
    """Find vehicles by color or plate text."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_find_vehicles(color, plate)))


@mcp.tool
def find_plates(text: str = "") -> str:
    """Find license plates and the vehicle they belong to."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_find_plates(text)))


@mcp.tool
def find_objects(label: str = "") -> str:
    """Find notable objects (bottle, bag, phone). Empty label lists all objects."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_find_objects(label)))


@mcp.tool
def object_appearances(label: str) -> str:
    """Clips and events where an object appears. Pass a label like bottle or an object_id."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_object_appearances(label)))


@mcp.tool
def search_transcript(phrase: str) -> str:
    """Find clips whose transcript contains a phrase, with clip clocks and uri."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_search_transcript(phrase)))


@mcp.tool
def clip_at_timestamp(timestamp: str) -> str:
    """Clip that covers a clock in the full video, e.g. 08:30."""
    blocked = _require_primer()
    if blocked:
        return blocked
    seek_s = _clock(timestamp, "timestamp")
    hits = []
    for row in _run(q_clip_at_timestamp(), limit=50):
        start = clock_seconds(row.get("start_timestamp"))
        end = clock_seconds(row.get("end_timestamp"))
        if start is None or end is None:
            continue
        if start <= seek_s <= end:
            hits.append(row)
    return dumps(hits or {"error": "no clip covers that timestamp"})


@mcp.tool
def event_chain(event_type: str = "") -> str:
    """Events linked by CONTINUES across adjacent clips (one stop spanning time). Optional type filter."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_event_chain(event_type)))


_KIND_FIELDS = {
    "tagged_splice": "tagged_splice_uri",
    "splice": "splice_uri",
    "clip": "clip_uri",
    "video": "clip_uri",
    "audio": "audio_uri",
    "folder": "clip_uri",
}


def _open_local(uri: str, open_file: bool = False) -> dict:
    local = to_local_path(uri)
    if local is None:
        return {"error": "No path provided"}
    if not local.exists():
        return {
            "error": "File not found on this machine",
            "graph_uri": uri,
            "local_path": str(local),
            "hint": "Graph URIs are /opt/airflow/data/... and remap to DATA_DIR (repo data/).",
        }
    if not allowed_path(local):
        return {"error": "Path is outside the video data folder", "local_path": str(local)}
    reveal_in_finder(local, open_file=open_file)
    return {
        "opened": True,
        "graph_uri": uri,
        "local_path": str(local),
        "revealed_in_finder": not open_file,
        "opened_file": open_file,
    }


@mcp.tool
def open_in_finder(uri: str, open_file: bool = False) -> str:
    """Reveal a graph attachment in Finder. Pass tagged_splice_uri, splice_uri, clip_uri, or audio_uri — including /opt/airflow/data/... paths. They remap to the local data/ folder. open_file=true opens the jpg/mp4 instead of only revealing it."""
    blocked = _require_primer()
    if blocked:
        return blocked
    if not (uri or "").strip():
        return dumps({"error": "uri is required", "hint": "Pass tagged_splice_uri or use open_clip_attachment"})
    try:
        return dumps(_open_local(uri.strip(), open_file=open_file))
    except Exception as exc:
        return dumps({"error": str(exc), "uri": uri})


@mcp.tool
def open_clip_attachment(
    clip: str,
    video_id: str = "",
    kind: str = "tagged_splice",
    open_file: bool = False,
) -> str:
    """Open a clip attachment in Finder. kind is tagged_splice (YOLO splice.jpg), splice, clip (video.mp4), audio, or folder. Example: clip=clip_0009 kind=tagged_splice."""
    blocked = _require_primer()
    if blocked:
        return blocked
    kind_key = (kind or "tagged_splice").strip().lower()
    field = _KIND_FIELDS.get(kind_key)
    if not field:
        return dumps({"error": f"Unknown kind {kind}", "kinds": sorted(_KIND_FIELDS)})
    rows = _run(q_clip_attachments(video_id, clip), limit=5)
    if not rows:
        return dumps({"error": "No clip matched", "clip": clip, "video_id": video_id})
    row = rows[0]
    uri = row.get(field)
    if kind_key == "splice" and not uri:
        uri = row.get("tagged_splice_uri")
    if kind_key == "tagged_splice" and not uri:
        uri = row.get("splice_uri")
    if not uri:
        return dumps({"error": f"Clip has no {kind_key} uri", "clip": row})
    if kind_key == "folder":
        local = to_local_path(uri)
        if local is None:
            return dumps({"error": "Could not map clip folder", "uri": uri})
        uri = str(local.parent)
    try:
        result = _open_local(uri, open_file=open_file)
    except Exception as exc:
        return dumps({"error": str(exc), "uri": uri, "clip": row})
    result["clip"] = row.get("clip")
    result["video_id"] = row.get("video_id")
    result["kind"] = kind_key
    return dumps(result)


def _clip_for_scene(clip: str, video_id: str, timestamp: str) -> dict | None:
    if clip.strip():
        rows = _run(q_clip_attachments(video_id, clip.strip()), limit=5)
        if rows:
            return rows[0]
    if timestamp.strip():
        seek_s = _clock(timestamp, "timestamp")
        for row in _run(q_clip_at_timestamp(), limit=80):
            if video_id and row.get("video_id") not in ("", video_id):
                continue
            start = clock_seconds(row.get("start_timestamp"))
            end = clock_seconds(row.get("end_timestamp"))
            if start is None or end is None:
                continue
            if start <= seek_s <= end:
                extra = _run(q_clip_attachments(row.get("video_id") or video_id, row["clip"]), limit=1)
                return extra[0] if extra else row
    return None


@mcp.tool
def analyze_scene(
    clip: str = "",
    timestamp: str = "",
    video_id: str = "",
    include_frames: bool = False,
    open_finder: bool = True,
) -> list:
    """Pull clip scene images into Codex for visual analysis (tagged splice.jpg, optional frame cells). Also reveals the splice in Finder. Pass clip=clip_0009 and/or timestamp=01:56. Look at the returned images: people, clothing, vehicles, objects, officer vs civilian."""
    blocked = _require_primer()
    if blocked:
        return [blocked]
    row = _clip_for_scene(clip, video_id, timestamp)
    if not row:
        return [dumps({"error": "No clip matched. Pass clip=clip_0009 or timestamp=01:56.", "clip": clip, "timestamp": timestamp})]
    images = clip_image_paths(row, include_frames=include_frames)
    if not images:
        return [dumps({"error": "No splice/frame images on disk for this clip", "clip": row})]
    if open_finder:
        try:
            reveal_in_finder(images[0], open_file=False)
        except Exception:
            pass
    note = dumps(
        {
            "instruction": "Analyze the attached scene image(s). tagged_splice is a YOLO-labeled grid of frames from this clip. Report observable facts only: who is present, clothes, vehicles, objects, officer vs civilian. Not a charge.",
            "video_id": row.get("video_id"),
            "clip": row.get("clip"),
            "start_timestamp": row.get("start_timestamp"),
            "end_timestamp": row.get("end_timestamp"),
            "local_paths": [str(path) for path in images],
            "clip_uri": row.get("clip_uri"),
            "tagged_splice_uri": row.get("tagged_splice_uri"),
            "opened_in_finder": open_finder,
        }
    )
    payload: list = [note]
    payload.extend(Image(path=path) for path in images)
    return payload


@mcp.tool
def rewrite_media_paths() -> str:
    """Rewrite Neo4j media URIs from /opt/airflow/data/... to this machine's data/ folder. Safe to re-run."""
    blocked = _require_primer()
    if blocked:
        return blocked
    try:
        return dumps(rewrite_graph_uris())
    except Exception as exc:
        return dumps({"error": str(exc)})


@mcp.tool
def run_custom_cypher(cypher: str, limit: int = 50) -> str:
    """Read-only custom Cypher when no prebuilt block fits. MATCH/RETURN only. No CREATE/MERGE/DELETE/SET. Call explain_graph_context first."""
    blocked = _require_primer()
    if blocked:
        return blocked
    text = (cypher or "").strip().rstrip(";")
    if not text:
        return dumps({"error": "cypher is required"})
    if _WRITE.search(text):
        return dumps(
            {
                "error": "Custom queries are read-only. Use MATCH/OPTIONAL MATCH/WITH/RETURN. No CREATE, MERGE, DELETE, SET, or DROP.",
            }
        )
    if ";" in text:
        return dumps({"error": "One Cypher statement only."})
    try:
        rows = run_cypher(text, {}, limit=limit)
    except Exception as exc:
        return dumps({"error": str(exc)})
    return dumps({"rows": rows, "n": len(rows)})


if __name__ == "__main__":
    mcp.run(transport="stdio")
