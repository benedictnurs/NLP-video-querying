from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastmcp import FastMCP

from graph_mcp.db import dumps, load_env, run_cypher
from graph_mcp.queries import (
    BLOCKS,
    clock_seconds,
    clip_at_timestamp as q_clip_at_timestamp,
    count_events as q_count_events,
    event_at_time as q_event_at_time,
    event_chain as q_event_chain,
    event_participants as q_event_participants,
    events_by_type as q_events_by_type,
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

load_env()

mcp = FastMCP(
    name="video-intel-graph",
    instructions=(
        "Police bodycam graph for investigative search. "
        "ALWAYS call explain_graph_context first in a session before any other tool. "
        "Prefer prebuilt query blocks. Use run_custom_cypher only for gaps. "
        "potential_suspect is a scene role, not a charge."
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
            "query_blocks": BLOCKS,
        }
    )


@mcp.tool
def list_query_blocks() -> str:
    """Catalog of prebuilt Neo4j query blocks. Use these before writing custom Cypher."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(BLOCKS)


@mcp.tool
def list_videos() -> str:
    """List ingested videos in the graph."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_list_videos()))


@mcp.tool
def count_events(event_type: str = "") -> str:
    """Count Event nodes by type. Empty event_type counts all types. Example: miranda_warning, arrest, traffic_stop."""
    blocked = _require_primer()
    if blocked:
        return blocked
    return dumps(_run(q_count_events(event_type)))


@mcp.tool
def events_in_timeframe(start_timestamp: str, end_timestamp: str, event_type: str = "") -> str:
    """Events whose start clock falls in a window, e.g. start_timestamp=01:00 end_timestamp=05:00. Optional event_type filter."""
    blocked = _require_primer()
    if blocked:
        return blocked
    start_s = _clock(start_timestamp, "start_timestamp")
    end_s = _clock(end_timestamp, "end_timestamp")
    return dumps(_run(q_events_in_timeframe(start_s, end_s, event_type)))


@mcp.tool
def events_by_type(event_type: str) -> str:
    """List every event of one type with clocks, clip, summary, and people. Use miranda_warning for all Miranda readings."""
    blocked = _require_primer()
    if blocked:
        return blocked
    if not event_type.strip():
        return dumps({"error": "event_type is required", "event_types": EVENT_TYPES})
    return dumps(_run(q_events_by_type(event_type)))


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
    mcp.run()
