from __future__ import annotations

from workers.clock import parse_clock

BLOCKS = {
    "count_events": {
        "title": "Count events by type",
        "use": "How many Miranda readings, arrests, traffic stops, etc.",
        "params": ["event_type"],
    },
    "events_in_timeframe": {
        "title": "Events in a time window",
        "use": "What happened between two clocks, e.g. 01:00 to 05:00.",
        "params": ["start_timestamp", "end_timestamp", "event_type"],
    },
    "events_by_type": {
        "title": "List events of one type",
        "use": "All miranda_warning / arrest / traffic_stop rows with clocks and people.",
        "params": ["event_type"],
    },
    "event_at_time": {
        "title": "Event nearest a timestamp",
        "use": "What is happening at 01:56.",
        "params": ["timestamp", "event_type"],
    },
    "event_participants": {
        "title": "People/vehicles/objects on an event",
        "use": "Who and what an event involves, with INVOLVES.role.",
        "params": ["event_type", "start_timestamp"],
    },
    "find_people": {
        "title": "Find people by appearance or role",
        "use": "Clothes, officer vs civilian, potential_suspect.",
        "params": ["clothes", "role", "is_cop", "potential_suspect"],
    },
    "find_potential_suspects": {
        "title": "Potential suspects with snapshots",
        "use": "Who the officer is stopping/arresting, description, why, when.",
        "params": [],
    },
    "person_timeline": {
        "title": "One person's events over time",
        "use": "Every event a person_id is involved in, ordered by clock.",
        "params": ["person_id"],
    },
    "find_vehicles": {
        "title": "Find vehicles",
        "use": "Color or plate.",
        "params": ["color", "plate"],
    },
    "find_plates": {
        "title": "Find license plates",
        "use": "Plate text and linked vehicle.",
        "params": ["text"],
    },
    "find_objects": {
        "title": "Find notable objects",
        "use": "Bottle, bag, phone, etc.",
        "params": ["label"],
    },
    "object_appearances": {
        "title": "Object tied to clips and events",
        "use": "When an object shows up and which events involve it.",
        "params": ["label"],
    },
    "search_transcript": {
        "title": "Search clip transcripts",
        "use": "Keyword in spoken audio, with clip clocks.",
        "params": ["phrase"],
    },
    "clip_at_timestamp": {
        "title": "Clip covering a clock",
        "use": "Which clip to open at 08:30.",
        "params": ["timestamp"],
    },
    "event_chain": {
        "title": "Continuing event chains",
        "use": "A traffic_stop or interrogation that spans clips.",
        "params": ["event_type"],
    },
    "find_events": {
        "title": "Find events by definition across videos",
        "use": "find all dui, all miranda rights, all traffic stops. Uses topics/aliases. Optional video_id.",
        "params": ["query", "video_id"],
    },
    "list_videos": {
        "title": "Ingested videos",
        "use": "What footage is in the graph.",
        "params": [],
    },
}


def clock_seconds(value) -> float | None:
    parsed = parse_clock(value)
    if parsed is None:
        return None
    if parsed < 1000 and str(value).isdigit():
        return float(parsed)
    return parsed / 1000.0


def count_events(event_type: str = "", video_id: str = ""):
    return (
        """
        MATCH (e:Event)
        WHERE ($event_type = '' OR e.type = $event_type)
          AND ($video_id = '' OR e.video_id = $video_id)
        RETURN e.type AS event_type, count(*) AS n
        ORDER BY n DESC
        """,
        {"event_type": (event_type or "").strip(), "video_id": (video_id or "").strip()},
    )


def events_in_timeframe(start_s: float, end_s: float, event_type: str = "", video_id: str = ""):
    return (
        """
        MATCH (e:Event)
        WHERE e.seek_s >= $start_s AND e.seek_s <= $end_s
          AND ($event_type = '' OR e.type = $event_type)
          AND ($video_id = '' OR e.video_id = $video_id)
        OPTIONAL MATCH (c:Clip)-[:CONTAINS]->(e)
        OPTIONAL MATCH (e)-[r:INVOLVES]->(p:Person)
        WITH e, c, collect(DISTINCT {id: p.local_id, role: r.role, clothes: p.clothes}) AS people
        RETURN e.video_id AS video_id,
               e.source_name AS source_name,
               e.type AS event_type,
               e.start_timestamp AS start_timestamp,
               e.end_timestamp AS end_timestamp,
               e.seek_s AS seek_s,
               e.summary AS summary,
               e.subject_ids AS subject_ids,
               c.local_id AS clip,
               c.clip_uri AS clip_uri,
               c.splice_uri AS splice_uri,
               c.tagged_splice_uri AS tagged_splice_uri,
               people
        ORDER BY e.video_id, e.seek_s
        """,
        {
            "start_s": start_s,
            "end_s": end_s,
            "event_type": (event_type or "").strip(),
            "video_id": (video_id or "").strip(),
        },
    )


def events_by_type(event_type: str, video_id: str = ""):
    return (
        """
        MATCH (e:Event {type: $event_type})
        WHERE $video_id = '' OR e.video_id = $video_id
        OPTIONAL MATCH (c:Clip)-[:CONTAINS]->(e)
        OPTIONAL MATCH (e)-[r:INVOLVES]->(p:Person)
        WITH e, c, collect(DISTINCT {id: p.local_id, role: r.role, clothes: p.clothes, potential_suspect: p.potential_suspect}) AS people
        RETURN e.video_id AS video_id,
               e.source_name AS source_name,
               e.type AS event_type,
               e.start_timestamp AS start_timestamp,
               e.end_timestamp AS end_timestamp,
               e.seek_s AS seek_s,
               e.summary AS summary,
               c.local_id AS clip,
               c.clip_uri AS clip_uri,
               c.splice_uri AS splice_uri,
               c.tagged_splice_uri AS tagged_splice_uri,
               people
        ORDER BY e.video_id, e.seek_s
        """,
        {"event_type": event_type.strip(), "video_id": (video_id or "").strip()},
    )


def events_by_types(event_types: list[str], video_id: str = ""):
    return (
        """
        MATCH (e:Event)
        WHERE e.type IN $event_types
          AND ($video_id = '' OR e.video_id = $video_id)
        OPTIONAL MATCH (c:Clip)-[:CONTAINS]->(e)
        OPTIONAL MATCH (e)-[:INSTANCE_OF]->(t:EventType)
        OPTIONAL MATCH (t)-[:IN_TOPIC]->(topic:Topic)
        OPTIONAL MATCH (e)-[r:INVOLVES]->(p:Person)
        WITH e, c, collect(DISTINCT topic.id) AS topics,
             collect(DISTINCT {id: p.local_id, role: r.role, clothes: p.clothes}) AS people
        RETURN e.video_id AS video_id,
               e.source_name AS source_name,
               e.type AS event_type,
               e.start_timestamp AS start_timestamp,
               e.end_timestamp AS end_timestamp,
               e.seek_s AS seek_s,
               e.summary AS summary,
               c.local_id AS clip,
               c.clip_uri AS clip_uri,
               c.splice_uri AS splice_uri,
               c.tagged_splice_uri AS tagged_splice_uri,
               topics,
               people
        ORDER BY e.video_id, e.seek_s
        """,
        {"event_types": event_types, "video_id": (video_id or "").strip()},
    )


def event_at_time(seek_s: float, event_type: str = ""):
    return (
        """
        MATCH (e:Event)
        WHERE $event_type = '' OR e.type = $event_type
        WITH e, abs(e.seek_s - $seek_s) AS delta
        ORDER BY delta
        LIMIT 8
        OPTIONAL MATCH (e)-[r:INVOLVES]->(p:Person)
        WITH e, delta, collect(DISTINCT {id: p.local_id, role: r.role, clothes: p.clothes}) AS people
        RETURN e.type AS event_type,
               e.start_timestamp AS start_timestamp,
               e.end_timestamp AS end_timestamp,
               e.seek_s AS seek_s,
               delta AS seconds_from_query,
               e.summary AS summary,
               people
        """,
        {"seek_s": seek_s, "event_type": (event_type or "").strip()},
    )


def event_participants(event_type: str, start_timestamp: str):
    return (
        """
        MATCH (e:Event)
        WHERE e.type = $event_type AND e.start_timestamp = $start_timestamp
        OPTIONAL MATCH (e)-[r:INVOLVES]->(n)
        RETURN e.type AS event_type,
               e.start_timestamp AS start_timestamp,
               e.end_timestamp AS end_timestamp,
               e.summary AS summary,
               labels(n)[0] AS kind,
               coalesce(n.local_id, n.label, n.color, n.text) AS name,
               r.role AS role,
               n.clothes AS clothes,
               n.description AS description,
               n.plate AS plate,
               n.distinctive AS distinctive
        """,
        {"event_type": event_type.strip(), "start_timestamp": start_timestamp.strip()},
    )


def find_people(clothes: str = "", role: str = "", is_cop=None, potential_suspect=None):
    return (
        """
        MATCH (p:Person)
        WHERE ($clothes = '' OR toLower(p.clothes) CONTAINS toLower($clothes))
          AND ($role = '' OR p.role = $role)
          AND ($is_cop IS NULL OR p.is_cop = $is_cop)
          AND ($potential_suspect IS NULL OR p.potential_suspect = $potential_suspect)
        RETURN p.local_id AS person_id,
               p.role AS role,
               p.is_cop AS is_cop,
               p.potential_suspect AS potential_suspect,
               p.clothes AS clothes,
               p.race AS race,
               p.gender AS gender,
               p.description AS description,
               p.suspect_snapshot AS suspect_snapshot,
               p.suspect_at AS suspect_at,
               p.suspect_event AS suspect_event
        ORDER BY p.local_id
        """,
        {
            "clothes": clothes or "",
            "role": role or "",
            "is_cop": is_cop,
            "potential_suspect": potential_suspect,
        },
    )


def find_potential_suspects():
    return (
        """
        MATCH (p:Person {potential_suspect: true})
        OPTIONAL MATCH (e:Event)-[r:INVOLVES]->(p)
        WHERE r.role = 'potential_suspect'
        WITH p, collect(DISTINCT {type: e.type, start_timestamp: e.start_timestamp}) AS events
        RETURN p.local_id AS person_id,
               p.clothes AS clothes,
               p.description AS description,
               p.suspect_snapshot AS snapshot,
               p.suspect_reason AS reason,
               p.suspect_at AS first_tagged_at,
               p.suspect_event AS first_tagged_event,
               events
        """,
        {},
    )


def person_timeline(person_id: str):
    return (
        """
        MATCH (p:Person)
        WHERE p.local_id = $person_id OR p.id ENDS WITH $person_id
        MATCH (e:Event)-[r:INVOLVES]->(p)
        RETURN p.local_id AS person_id,
               p.role AS person_role,
               e.type AS event_type,
               e.start_timestamp AS start_timestamp,
               e.end_timestamp AS end_timestamp,
               r.role AS involves_role,
               e.summary AS summary
        ORDER BY e.seek_s
        """,
        {"person_id": person_id.strip()},
    )


def find_vehicles(color: str = "", plate: str = ""):
    return (
        """
        MATCH (v:Vehicle)
        WHERE ($color = '' OR toLower(v.color) CONTAINS toLower($color))
          AND ($plate = '' OR v.plate CONTAINS toUpper($plate))
        OPTIONAL MATCH (v)-[:HAS_PLATE]->(p:Plate)
        RETURN v.id AS vehicle_id,
               v.color AS color,
               v.plate AS plate,
               v.analysis AS analysis,
               collect(p.text) AS plates
        """,
        {"color": color or "", "plate": plate or ""},
    )


def find_plates(text: str = ""):
    return (
        """
        MATCH (p:Plate)
        WHERE $text = '' OR p.text CONTAINS toUpper($text)
        OPTIONAL MATCH (v:Vehicle)-[:HAS_PLATE]->(p)
        RETURN p.text AS plate,
               p.confidence AS confidence,
               v.color AS vehicle_color,
               v.id AS vehicle_id
        """,
        {"text": text or ""},
    )


def find_objects(label: str = ""):
    return (
        """
        MATCH (o:Object)
        WHERE $label = '' OR toLower(o.label) CONTAINS toLower($label)
        RETURN o.local_id AS object_id,
               o.label AS label,
               o.distinctive AS distinctive
        """,
        {"label": label or ""},
    )


def object_appearances(label: str):
    return (
        """
        MATCH (o:Object)
        WHERE toLower(o.label) CONTAINS toLower($label) OR o.local_id = $label
        OPTIONAL MATCH (c:Clip)-[:CONTAINS]->(o)
        OPTIONAL MATCH (e:Event)-[:INVOLVES]->(o)
        RETURN o.local_id AS object_id,
               o.label AS label,
               o.distinctive AS distinctive,
               collect(DISTINCT c.local_id) AS clips,
               collect(DISTINCT {type: e.type, start_timestamp: e.start_timestamp}) AS events
        """,
        {"label": label.strip()},
    )


def search_transcript(phrase: str):
    return (
        """
        MATCH (c:Clip)
        WHERE toLower(c.transcript) CONTAINS toLower($phrase)
        RETURN c.local_id AS clip,
               c.start_timestamp AS start_timestamp,
               c.end_timestamp AS end_timestamp,
               c.summary AS summary,
               c.clip_uri AS clip_uri,
               c.splice_uri AS splice_uri,
               c.tagged_splice_uri AS tagged_splice_uri
        ORDER BY c.index
        """,
        {"phrase": phrase.strip()},
    )


def clip_at_timestamp():
    return (
        """
        MATCH (c:Clip)
        RETURN c.video_id AS video_id,
               c.local_id AS clip,
               c.start_timestamp AS start_timestamp,
               c.end_timestamp AS end_timestamp,
               c.summary AS summary,
               c.clip_uri AS clip_uri,
               c.splice_uri AS splice_uri,
               c.tagged_splice_uri AS tagged_splice_uri,
               c.index AS index
        ORDER BY c.index
        """,
        {},
    )


def event_chain(event_type: str = ""):
    return (
        """
        MATCH path = (a:Event)-[:CONTINUES*]->(b:Event)
        WHERE $event_type = '' OR a.type = $event_type
        RETURN a.type AS event_type,
               a.start_timestamp AS chain_start,
               b.start_timestamp AS chain_end,
               length(path) AS hops
        ORDER BY a.seek_s
        """,
        {"event_type": (event_type or "").strip()},
    )


def list_videos():
    return (
        """
        MATCH (v:Video)
        OPTIONAL MATCH (v)-[:HAS_PERSON]->(p:Person)
        OPTIONAL MATCH (v)-[:HAS_VEHICLE]->(car:Vehicle)
        WITH v,
             count(DISTINCT p) AS people,
             count(DISTINCT car) AS vehicles
        RETURN v.id AS video_id,
               v.source_name AS source_name,
               v.duration_s AS duration_s,
               v.clip_count AS clip_count,
               v.status AS status,
               people,
               vehicles
        """,
        {},
    )


def clip_attachments(video_id: str = "", clip: str = ""):
    return (
        """
        MATCH (c:Clip)
        WHERE ($video_id = '' OR c.video_id = $video_id)
          AND ($clip = '' OR c.local_id = $clip OR c.id ENDS WITH $clip)
        RETURN c.video_id AS video_id,
               c.local_id AS clip,
               c.start_timestamp AS start_timestamp,
               c.end_timestamp AS end_timestamp,
               c.clip_uri AS clip_uri,
               c.splice_uri AS splice_uri,
               c.tagged_splice_uri AS tagged_splice_uri,
               c.audio_uri AS audio_uri
        ORDER BY c.video_id, c.index
        """,
        {"video_id": (video_id or "").strip(), "clip": (clip or "").strip()},
    )


def inventory():
    return (
        """
        MATCH (n)
        WITH labels(n)[0] AS label, count(*) AS n
        RETURN label, n
        ORDER BY n DESC
        """,
        {},
    )
